from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import asyncio
import subprocess
import os
import hashlib
import hmac
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List

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

downloads: Dict[str, Dict] = {}
download_counts: Dict[str, int] = {}
config = {
    "download_dir": str(Path.home() / "Downloads" / "MusicDownloader"),
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

def get_download_stats():
    download_dir = Path(config["download_dir"])
    files = list(download_dir.glob("*.mp3"))
    
    total_size = sum(f.stat().st_size for f in files)
    
    # Calculate download speed and remaining time
    active_downloads = [d for d in downloads.values() if d["status"] == "downloading"]
    total_progress = sum(d.get("progress", 0) for d in active_downloads)
    avg_speed = sum(d.get("speed", 0) for d in active_downloads)
    
    stats = {
        "total_files": len(files),
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "active_downloads": len(active_downloads),
        "average_speed_mbps": round(avg_speed / (1024 * 1024), 2) if avg_speed else 0,
        "recent_files": []
    }
    
    recent_files = sorted(files, key=lambda x: x.stat().st_mtime, reverse=True)[:10]
    for file in recent_files:
        fstat = file.stat()
        stats["recent_files"].append({
            "name": file.name,
            "size_mb": round(fstat.st_size / (1024 * 1024), 2),
            "date": datetime.fromtimestamp(fstat.st_mtime).strftime("%Y-%m-%d %H:%M")
        })
    
    return stats

async def download_url(url: str) -> None:
    try:
        if not url or not isinstance(url, str):
            raise ValueError("Invalid URL")
            
        url = url.strip()
        logging.info(f"Starting download for: {url}")
        
        total_songs = download_counts.get(url, 1)
        current_song = 0
        
        if "spotify.com" in url:
            command = [
                "spotdl",
                url,
                "--output", config["download_dir"],
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
                "-o", f"{config['download_dir']}/%(title)s.%(ext)s",
            ]

        logging.debug(f"Running command: {' '.join(command)}")
        downloads[url] = {
            "status": "downloading",
            "progress": 0,
            "speed": 0,
            "total_songs": total_songs,
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
                        parts = line.split()
                        for i, part in enumerate(parts):
                            if "%" in part:
                                progress = float(part.rstrip("%"))
                                if i + 1 < len(parts) and "EiB/s" in parts[i+1]:
                                    speed = float(parts[i+1].split("EiB/s")[0])
                                    downloads[url]["speed"] = speed
                                
                                # Calculate overall progress based on current song and total songs
                                overall_progress = (current_song * 100 + progress) / total_songs
                                downloads[url]["progress"] = overall_progress
                                break
                    except:
                        pass
                elif "Downloading" in line or "[download] Destination" in line:
                    current_song += 1
                    downloads[url]["completed_songs"] = current_song
            except:
                continue

        await process.wait()
        
        if process.returncode == 0:
            downloads[url] = {
                "status": "completed",
                "progress": 100,
                "speed": 0,
                "total_songs": total_songs,
                "completed_songs": total_songs
            }
            logging.info(f"Successfully downloaded: {url}")
        else:
            error = await process.stderr.read()
            error_text = error.decode().strip() if error else "Download failed"
            downloads[url] = {
                "status": "failed",
                "error": error_text,
                "progress": 0,
                "speed": 0,
                "total_songs": total_songs,
                "completed_songs": current_song
            }
            logging.error(f"Failed to download {url}: {error_text}")

    except Exception as e:
        logging.error(f"Error downloading {url}: {str(e)}")
        downloads[url] = {
            "status": "failed",
            "error": str(e),
            "progress": 0,
            "speed": 0,
            "total_songs": 1,
            "completed_songs": 0
        }

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
        
        # Use cached song counts from the check phase
        total_songs = sum(download_counts.get(url, 1) for url in urls)
            
        if total_songs > config["max_free_downloads"]:
            if not verify_password(password):
                raise HTTPException(
                    status_code=403,
                    detail="Password required for more than 5 songs. DM me on Twitter @didntdrinkwater for the password!"
                )
        
        logging.info(f"Starting downloads for URLs: {urls}")
        
        for url in urls:
            asyncio.create_task(download_url(url))
        
        return {"message": "Downloads started"}
    except HTTPException as he:
        raise he
    except Exception as e:
        logging.error(f"Error starting downloads: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/status")
async def get_status():
    return downloads

@app.get("/api/stats")
async def get_stats():
    try:
        stats = get_download_stats()
        return stats
    except Exception as e:
        logging.error(f"Error getting stats: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/config")
async def get_config():
    return {"max_free_downloads": config["max_free_downloads"]}

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
