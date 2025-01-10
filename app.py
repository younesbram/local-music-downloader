from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import asyncio
import subprocess
import os
import hashlib
import hmac
import logging
import json
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, Set

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('downloads.log'),
        logging.StreamHandler()
    ]
)

class StatsManager:
    def __init__(self, stats_file="/app/data/global_stats.json"):
        self.stats_file = stats_file
        Path(stats_file).parent.mkdir(parents=True, exist_ok=True)
        self.ensure_file_exists()
        
    def ensure_file_exists(self):
        try:
            with open(self.stats_file, 'r') as f:
                json.load(f)
        except:
            self.save_stats({
                "total_downloads": 0,
                "total_songs": 0,
                "unique_visitors": []
            })
    
    def load_stats(self):
        with open(self.stats_file, 'r') as f:
            return json.load(f)
    
    def save_stats(self, stats):
        with open(self.stats_file, 'w') as f:
            json.dump(stats, f)
    
    def update_stats(self, new_downloads=0, new_songs=0, visitor_ip=None):
        stats = self.load_stats()
        stats["total_downloads"] += new_downloads
        stats["total_songs"] += new_songs
        if visitor_ip and visitor_ip not in stats["unique_visitors"]:
            stats["unique_visitors"].append(visitor_ip)
        self.save_stats(stats)
        return stats

downloads: Dict[str, Dict] = {}
download_counts: Dict[str, int] = {}
stats_manager = StatsManager()

config = {
    "download_dir": "/app/downloads",
    "password_hash": hashlib.sha256(os.getenv("APP_PASSWORD", "default").encode()).hexdigest(),
    "max_free_downloads": 5
}

Path(config["download_dir"]).mkdir(parents=True, exist_ok=True)

def verify_password(password: str) -> bool:
    if not password:
        return False
    test_hash = hashlib.sha256(password.encode()).hexdigest()
    return hmac.compare_digest(test_hash, config["password_hash"])

async def count_songs_in_url(url: str) -> int:
    try:
        if "spotify.com" in url:
            process = await asyncio.create_subprocess_exec(
                "spotdl", url, "--list",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
        else:
            process = await asyncio.create_subprocess_exec(
                "yt-dlp", "--flat-playlist", "--dump-json", url,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
        stdout, _ = await process.communicate()
        output = stdout.decode()
        
        if "spotify.com" in url:
            songs = len([line for line in output.split('\n') if line.startswith('https://')])
            download_counts[url] = songs
            return songs
        else:
            songs = len([line for line in output.split('\n') if line.strip()])
            download_counts[url] = songs
            return songs
            
    except Exception as e:
        logging.error(f"Error counting songs: {str(e)}")
        download_counts[url] = 1
        return 1

def get_download_stats(request: Request):
    stats = stats_manager.load_stats()
    download_dir = Path(config["download_dir"])
    files = list(download_dir.glob("*.mp3"))
    
    active_downloads = [d for d in downloads.values() if d["status"] == "downloading"]
    
    return {
        "total_files": len(files),
        "total_size_mb": round(sum(f.stat().st_size for f in files if f.stat().st_size > 0) / (1024 * 1024), 2),
        "active_downloads": len(active_downloads),
        "global_downloads": stats["total_downloads"],
        "unique_users": len(stats["unique_visitors"]),
        "download_ready": any(d["status"] == "completed" for d in downloads.values())
    }

async def download_url(url: str, download_id: str) -> None:
    try:
        if not url or not isinstance(url, str):
            raise ValueError("Invalid URL")
            
        url = url.strip()
        logging.info(f"Starting download for: {url}")
        
        download_path = Path(config["download_dir"]) / download_id
        download_path.mkdir(parents=True, exist_ok=True)
        
        if "spotify.com" in url:
            command = [
                "spotdl",
                url,
                "--output", str(download_path),
                "--format", "mp3",
                "--bitrate", "320k",
                "--threads", "4",
            ]
        else:
            command = [
                "yt-dlp",
                url,
                "-f", "bestaudio/best",
                "--extract-audio",
                "--audio-format", "mp3",
                "--audio-quality", "0",
                "--embed-thumbnail",
                "--add-metadata",
                "--prefer-ffmpeg",
                "-o", f"{str(download_path)}/%(title)s.%(ext)s",
            ]

        logging.debug(f"Running command: {' '.join(command)}")
        downloads[url] = {
            "status": "downloading",
            "progress": 0,
            "download_id": download_id,
            "total_songs": download_counts.get(url, 1),
            "completed_songs": 0
        }
        
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        while True:
            line = await process.stdout.readline()
            if not line:
                break
                
            try:
                line = line.decode().strip()
                if "[download]" in line and "%" in line:
                    try:
                        progress = float(line.split("%")[0].strip().split()[-1])
                        downloads[url]["progress"] = progress
                    except:
                        pass
                elif "[download] Destination" in line or "Downloading" in line:
                    downloads[url]["completed_songs"] += 1
            except:
                continue

        await process.wait()
        
        if process.returncode == 0:
            downloads[url].update({
                "status": "completed",
                "progress": 100,
                "completed_songs": download_counts.get(url, 1)
            })
            logging.info(f"Successfully downloaded: {url}")
        else:
            error = await process.stderr.read()
            error_text = error.decode().strip() if error else "Download failed"
            downloads[url].update({
                "status": "failed",
                "error": error_text,
                "progress": 0
            })
            logging.error(f"Failed to download {url}: {error_text}")

    except Exception as e:
        logging.error(f"Error downloading {url}: {str(e)}")
        downloads[url].update({
            "status": "failed",
            "error": str(e),
            "progress": 0
        })

@app.get("/", response_class=HTMLResponse)
async def get_html():
    with open('index.html', 'r') as f:
        return HTMLResponse(content=f.read())

@app.post("/api/check-urls")
async def check_urls(request: Request):
    try:
        data = await request.json()
        urls = data.get("urls", [])
        password = data.get("password", "")
        
        if not urls:
            raise HTTPException(status_code=400, detail="No URLs provided")
        
        total_songs = 0
        for url in urls:
            song_count = await count_songs_in_url(url)
            total_songs += song_count
            
        needs_password = total_songs > config["max_free_downloads"]
        
        if needs_password:
            if password:
                if not verify_password(password):
                    raise HTTPException(
                        status_code=403,
                        detail="Invalid password. Please try again or DM @didntdrinkwater on Twitter for the password."
                    )
            
        return {
            "total_songs": total_songs,
            "needs_password": needs_password
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        logging.error(f"Error checking URLs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/download")
async def start_download(request: Request):
    try:
        data = await request.json()
        urls = data.get("urls", [])
        password = data.get("password", "")
        
        if not urls:
            raise HTTPException(status_code=400, detail="No URLs provided")
        
        total_songs = sum(download_counts.get(url, 1) for url in urls)
            
        if total_songs > config["max_free_downloads"]:
            if not verify_password(password):
                raise HTTPException(
                    status_code=403,
                    detail="Password required for more than 5 songs. DM me on Twitter @didntdrinkwater for the password!"
                )
        
        download_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        logging.info(f"Starting downloads for URLs: {urls}")
        
        for url in urls:
            asyncio.create_task(download_url(url, download_id))
        
        # Update global stats
        stats_manager.update_stats(
            new_downloads=len(urls),
            new_songs=total_songs,
            visitor_ip=request.client.host
        )
        
        return {"message": "Downloads started", "download_id": download_id}
    except HTTPException as he:
        raise he
    except Exception as e:
        logging.error(f"Error starting downloads: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/status")
async def get_status():
    return downloads

@app.get("/api/stats")
async def get_stats(request: Request):
    try:
        stats = get_download_stats(request)
        return stats
    except Exception as e:
        logging.error(f"Error getting stats: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/config")
async def get_config():
    return {"max_free_downloads": config["max_free_downloads"]}

@app.get("/api/download/{download_id}")
async def get_download(download_id: str):
    try:
        # Create zip file from download directory
        download_dir = Path(config["download_dir"]) / download_id
        if not download_dir.exists():
            raise HTTPException(status_code=404, detail="Download not found")
            
        zip_path = Path(config["download_dir"]) / f"{download_id}.zip"
        if not zip_path.exists():
            shutil.make_archive(str(zip_path.with_suffix('')), 'zip', download_dir)
        
        return FileResponse(
            path=zip_path,
            filename=f"music_download_{download_id}.zip",
            media_type="application/zip"
        )
    except HTTPException as he:
        raise he
    except Exception as e:
        logging.error(f"Error delivering download: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    print(f"""
╔════════════════════════════════════════╗
║     High Quality Music Downloader      ║
║----------------------------------------║
║ Server running at:                     ║
║ http://localhost:{port}                  ║
║                                        ║
║ Downloads will be saved to:            ║
║ {config["download_dir"]}
║                                        ║
║ Press Ctrl+C to quit                   ║
╚════════════════════════════════════════╝
""")
    uvicorn.run(app, host="0.0.0.0", port=port)
