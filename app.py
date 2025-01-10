from fastapi import FastAPI, HTTPException, Request, Response, Cookie
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
from typing import Dict, Optional
from uuid import uuid4

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

# Global state with session support
downloads: Dict[str, Dict[str, Dict]] = {}  # session_id -> url -> status
download_counts: Dict[str, int] = {}

config = {
    "download_dir": "/app/downloads",
    "password_hash": hashlib.sha256(os.getenv("APP_PASSWORD", "default").encode()).hexdigest(),
    "max_free_downloads": 5
}

# Ensure download directory exists
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

def get_download_stats(session_id: str):
    download_dir = Path(config["download_dir"])
    files = list(download_dir.glob("*.mp3"))
    
    # Only count active downloads for this session
    active_downloads = len([d for d in downloads.get(session_id, {}).values() 
                          if d["status"] == "downloading"])
    
    return {
        "total_files": len(files),
        "total_size_mb": round(sum(f.stat().st_size for f in files) / (1024 * 1024), 2),
        "active_downloads": active_downloads,
        "global_downloads": sum(len(session_downloads) for session_downloads in downloads.values()),
        "session_downloads": len(downloads.get(session_id, {}))
    }

async def download_url(url: str, download_id: str, session_id: str) -> None:
    if session_id not in downloads:
        downloads[session_id] = {}
        
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
        downloads[session_id][url] = {
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
                        downloads[session_id][url]["progress"] = progress
                    except:
                        pass
                elif "[download] Destination" in line or "Downloading" in line:
                    downloads[session_id][url]["completed_songs"] += 1
            except:
                continue

        await process.wait()
        
        if process.returncode == 0:
            downloads[session_id][url].update({
                "status": "completed",
                "progress": 100,
                "completed_songs": download_counts.get(url, 1)
            })
            logging.info(f"Successfully downloaded: {url}")
        else:
            error = await process.stderr.read()
            error_text = error.decode().strip() if error else "Download failed"
            downloads[session_id][url].update({
                "status": "failed",
                "error": error_text,
                "progress": 0
            })
            logging.error(f"Failed to download {url}: {error_text}")

    except Exception as e:
        logging.error(f"Error downloading {url}: {str(e)}")
        downloads[session_id][url] = {
            "status": "failed",
            "error": str(e),
            "progress": 0,
            "download_id": download_id,
            "total_songs": 1,
            "completed_songs": 0
        }

@app.get("/api/session")
async def get_session(response: Response, session_id: Optional[str] = Cookie(None)):
    if not session_id:
        session_id = str(uuid4())
        response.set_cookie(key="session_id", value=session_id)
    return {"session_id": session_id}

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
        
        if needs_password and password:
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
async def start_download(
    request: Request, 
    session_id: Optional[str] = Cookie(None)
):
    if not session_id:
        raise HTTPException(status_code=400, detail="No session ID")
        
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
            asyncio.create_task(download_url(url, download_id, session_id))
        
        return {"message": "Downloads started", "download_id": download_id}
    except HTTPException as he:
        raise he
    except Exception as e:
        logging.error(f"Error starting downloads: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/status")
async def get_status(session_id: Optional[str] = Cookie(None)):
    if not session_id:
        return {}
    return downloads.get(session_id, {})

@app.get("/api/stats")
async def get_stats(session_id: Optional[str] = Cookie(None)):
    try:
        if not session_id:
            return {}
        stats = get_download_stats(session_id)
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
    uvicorn.run(app, host="0.0.0.0", port=port)
