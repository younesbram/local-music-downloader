from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import asyncio
import subprocess
import os
import json
import socket
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict
import webbrowser

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # Set to DEBUG for more info
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('downloads.log'),
        logging.StreamHandler()
    ]
)

# Global state
downloads = {}
config = {
    "download_dir": str(Path.home() / "Downloads" / "MusicDownloader")
}

# Ensure download directory exists
Path(config["download_dir"]).mkdir(parents=True, exist_ok=True)

def get_download_stats():
    """Get stats about downloads folder"""
    download_dir = Path(config["download_dir"])
    files = list(download_dir.glob("*.mp3"))
    
    stats = {
        "total_files": len(files),
        "total_size_mb": round(sum(f.stat().st_size for f in files) / (1024 * 1024), 2),
        "recent_files": []
    }
    
    # Get 10 most recent files
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
    """Handle downloading a single URL with high quality settings."""
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
            universal_newlines=False  # Changed this
        )

        while True:
            line = await process.stdout.readline()
            if not line:
                break
                
            try:
                line = line.decode().strip()
                
                # Progress tracking
                if "[download]" in line and "%" in line:
                    try:
                        progress = float(line.split("%")[0].strip().split()[-1])
                        downloads[url]["progress"] = progress
                        logging.debug(f"Progress: {progress}%")
                    except:
                        pass
                elif "Downloading" in line:
                    downloads[url]["progress"] = 50  # Show some progress for Spotify
            except:
                continue

        await process.wait()
        
        if process.returncode == 0:
            downloads[url] = {"status": "completed", "progress": 100}
            logging.info(f"Successfully downloaded: {url}")
        else:
            error = await process.stderr.read()
            try:
                error_text = error.decode().strip()
            except:
                error_text = "Download failed"
                
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

@app.post("/api/download")
async def start_download(request: Request):
    try:
        data = await request.json()
        urls = data.get("urls", [])
        
        if not urls:
            raise HTTPException(status_code=400, detail="No URLs provided")
        
        logging.info(f"Starting downloads for URLs: {urls}")
        
        # Start downloads
        for url in urls:
            asyncio.create_task(download_url(url))
        
        return {"message": "Downloads started"}
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

@app.post("/api/config/download_dir")
async def set_download_dir(request: Request):
    try:
        data = await request.json()
        new_dir = data.get("path")
        if not new_dir:
            raise HTTPException(status_code=400, detail="Invalid path")
            
        config["download_dir"] = new_dir
        Path(new_dir).mkdir(parents=True, exist_ok=True)
        logging.info(f"Download directory updated to: {new_dir}")
        return {"message": "Download directory updated"}
    except Exception as e:
        logging.error(f"Error updating download directory: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/config")
async def get_config():
    return config

@app.post("/api/open_folder")
async def open_folder():
    try:
        if os.name == 'nt':  # Windows
            os.startfile(config["download_dir"])
        else:  # macOS and Linux
            webbrowser.open('file://' + config["download_dir"])
        return {"message": "Opened downloads folder"}
    except Exception as e:
        logging.error(f"Error opening folder: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    port = 8000
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
    uvicorn.run("app:app", host="0.0.0.0", port=port, log_level="info")
