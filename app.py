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

downloads = {}
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
            return len([line for line in output.split('\n') if line.startswith('https://')])
        else:
            return len([line for line in output.split('\n') if line.strip()])
            
    except Exception as e:
        logging.error(f"Error counting songs: {str(e)}")
        return 1

def get_download_stats():
    download_dir = Path(config["download_dir"])
    files = list(download_dir.glob("*.mp3"))
    
    stats = {
        "total_files": len(files),
        "total_size_mb": round(sum(f.stat().st_size for f in files) / (1024 * 1024), 2),
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
        downloads[url] = {"status": "downloading", "progress": 0}
        
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
                        logging.debug(f"Progress: {progress}%")
                    except:
                        pass
                elif "Downloading" in line:
                    downloads[url]["progress"] = 50
            except:
                continue

        await process.wait()
        
        if process.returncode == 0:
            downloads[url] = {"status": "completed", "progress": 100}
            logging.info(f"Successfully downloaded: {url}")
        else:
            error = await process.stderr.read()
            error_text = error.decode().strip() if error else "Download failed"
            downloads[url] = {
                "status": "failed",
                "error": error_text,
                "progress": 0
            }
            logging.error(f"Failed to download {url}: {error_text}")

    except Exception as e:
        logging.error(f"Error downloading {url}: {str(e)}")
        downloads[url] = {
            "status": "failed",
            "error": str(e),
            "progress": 0
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
        
        if not urls:
            raise HTTPException(status_code=400, detail="No URLs provided")
        
        total_songs = 0
        for url in urls:
            song_count = await count_songs_in_url(url)
            total_songs += song_count
            
        return {
            "total_songs": total_songs,
            "needs_password": total_songs > config["max_free_downloads"]
        }
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
            
        total_songs = 0
        for url in urls:
            song_count = await count_songs_in_url(url)
            total_songs += song_count
            
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
    return config

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
