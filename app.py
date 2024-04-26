import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

def download_url(url, download_dir):
    # Prepare the output path depending on the tool used
    if "spotify.com" in url:
        command = ["spotdl", url]
        cwd = download_dir
    else:
        # yt-dlp allows specifying output path and filename directly in the command
        output_path = os.path.join(download_dir, "%(title)s.%(ext)s")
        command = ["yt-dlp", "-f", "bestaudio", "--extract-audio", "--audio-format", "mp3", "--audio-quality", "0", "-o", output_path, url]
        cwd = None  # No need to change cwd for yt-dlp

    # Execute the command
    result = subprocess.run(command, stdout=subprocess.PIPE, text=True, stderr=subprocess.PIPE, cwd=cwd)
    return {'url': url, 'status': 'success' if result.returncode == 0 else 'failed', 'output': result.stdout.strip(), 'error': result.stderr.strip()}

def main():
    download_dir = 'downloads'
    # Ensure download directory exists
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)

    urls = []
    print("Enter the URLs (empty line to finish):")
    while True:
        url = input()
        if not url:
            break
        urls.append(url)

    with ThreadPoolExecutor(max_workers=4) as executor:
        # Submit all download tasks
        futures = [executor.submit(download_url, url, download_dir) for url in urls]
        # Initialize tqdm progress bar
        for future in tqdm(as_completed(futures), total=len(urls), desc="Downloading", unit="file"):
            result = future.result()
            if result['status'] == 'success':
                print(f"\nDownloaded: {result['url']}")
            else:
                print(f"Failed to download {result['url']}: {result['error']}")

    print("All downloads are complete.")

if __name__ == "__main__":
    main()
