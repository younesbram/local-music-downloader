
from flask import Flask, render_template, request, jsonify
import subprocess
import shlex

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def download():
    urls = request.json['urls']
    responses = []
    for url in urls:
        if "spotify.com" in url:
            command = f"spotdl {shlex.quote(url)}"
        else:
            command = f"yt-dlp -f bestaudio --extract-audio --audio-format mp3 --audio-quality 0 {shlex.quote(url)}"
        process = subprocess.run(command, shell=True, text=True, capture_output=True)
        responses.append({'url': url, 'status': 'success' if process.returncode == 0 else 'failed', 'output': process.stdout})
    return jsonify(responses)

if __name__ == '__main__':
    app.run(debug=True)
