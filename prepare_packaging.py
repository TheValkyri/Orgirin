import os
import shutil
import subprocess
import sys

def prepare_packaging():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    pkg_ffmpeg_dir = os.path.join(project_dir, "packaging", "ffmpeg")
    os.makedirs(pkg_ffmpeg_dir, exist_ok=True)
    
    # Locate and copy both ffmpeg.exe and ffprobe.exe (A-01: both are required)
    for binary_name in ("ffmpeg", "ffprobe"):
        exe_name = f"{binary_name}.exe"
        system_path = shutil.which(binary_name)
        if not system_path:
            res = subprocess.run(["where.exe", binary_name], capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                system_path = res.stdout.strip().splitlines()[0]

        if system_path and os.path.exists(system_path):
            dst = os.path.join(pkg_ffmpeg_dir, exe_name)
            print(f"Copying {exe_name} from {system_path} to {dst}...")
            shutil.copy2(system_path, dst)
        else:
            print(f"ERROR: {exe_name} not found on system path for bundling!")
            sys.exit(1)

        
    readme_path = os.path.join(pkg_ffmpeg_dir, "README_FFMPEG.txt")
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write("""FFmpeg License & Distribution Notice:

This software bundle includes a binary distribution of FFmpeg (https://ffmpeg.org).
FFmpeg is a trademark of Fabrice Bellard, originator of the FFmpeg project.

License: GNU General Public License (GPL) / Lesser General Public License (LGPL).
The source code for FFmpeg can be downloaded at https://ffmpeg.org/download.html.
No modifications were made to the FFmpeg binary executable included herein.
""")
    print("README_FFMPEG.txt written successfully.")

if __name__ == "__main__":
    prepare_packaging()
