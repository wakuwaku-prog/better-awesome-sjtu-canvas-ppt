#!/usr/bin/env python3
"""
Download PPT images from SJTU Canvas captured API JSON.

Usage:
    python download_ppt.py <json_file> <output_dir>

JSON format (one slide per entry):
    [{"s": "343551", "u": "https://...s3.../file.jpg?X-Amz-..."}, ...]

- "s": createSec in milliseconds (e.g. "343551" = 5min 43.551sec → filename 05_43.jpg)
- "u": S3 signed URL (may include :443 port which is stripped before download)
"""
import json
import os
import sys
import ssl
import urllib.request


def ms_to_mmss(ms_str):
    """Convert millisecond string to MM_SS format for filename."""
    total_sec = int(ms_str) // 1000
    minutes = total_sec // 60
    seconds = total_sec % 60
    return f"{minutes:02d}_{seconds:02d}"


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <json_file> <output_dir>")
        sys.exit(1)

    json_path = sys.argv[1]
    output_dir = sys.argv[2]

    # Read JSON, handling potential trailing text (e.g. opencli update messages)
    with open(json_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Find JSON array boundaries
    arr_start = content.index("[")
    arr_end = content.rindex("]")
    slides = json.loads(content[arr_start : arr_end + 1])

    os.makedirs(output_dir, exist_ok=True)
    ssl_ctx = ssl.create_default_context()

    print(f"Slides: {len(slides)}")
    print(f"Output: {output_dir}\n")

    success = 0
    for i, slide in enumerate(slides):
        create_sec = slide["s"]
        img_url = slide["u"]

        # Remove :443 port (default HTTPS, may cause issues on some networks)
        clean_url = img_url.replace(":443/", "/")

        time_str = ms_to_mmss(create_sec)
        filename = f"{time_str}.jpg"
        filepath = os.path.join(output_dir, filename)

        def do_download(url):
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, context=ssl_ctx, timeout=60) as resp:
                return resp.read()

        try:
            data = do_download(clean_url)
            with open(filepath, "wb") as f:
                f.write(data)
            print(f"  [{i+1:02d}/{len(slides)}] {time_str.replace('_', ':')}  OK  {len(data)//1024}KB")
            success += 1
        except Exception:
            try:
                data = do_download(img_url)
                with open(filepath, "wb") as f:
                    f.write(data)
                print(f"  [{i+1:02d}/{len(slides)}] {time_str.replace('_', ':')}  OK  {len(data)//1024}KB (retry)")
                success += 1
            except Exception as e:
                print(f"  [{i+1:02d}/{len(slides)}] {time_str.replace('_', ':')}  FAIL  {e}")

    print(f"\nDone: {success}/{len(slides)}")


if __name__ == "__main__":
    main()
