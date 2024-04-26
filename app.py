import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

def download_url(url):
    if "spotify.com" in url:
        # Download from Spotify
        command = ["spotdl", url]
    else:
        # Download from YouTube/SoundCloud
        command = ["yt-dlp", "-f", "bestaudio", "--extract-audio", "--audio-format", "mp3", "--audio-quality", "0", url]

    result = subprocess.run(command, stdout=subprocess.PIPE, text=True, stderr=subprocess.PIPE)
    return {'url': url, 'status': 'success' if result.returncode == 0 else 'failed', 'output': result.stdout.strip(), 'error': result.stderr.strip()}

def main():
    urls = []
    print("Enter the URLs (press enter on empty line to finish):")
    while True:
        url = input()
        if not url:
            break
        urls.append(url)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(download_url, url) for url in urls]
        for future in tqdm(as_completed(futures), total=len(urls), desc="Downloading", unit="file"):
            result = future.result()
            if result['status'] == 'success':
                print(f"\nDownloaded: {result['url']}")
            else:
                print(f"Failed to download {result['url']}: {result['error']}")

    print("All downloads are complete. \nThis code is for research purposes only -Younes Brahimi.")

if __name__ == "__main__":
    main()
