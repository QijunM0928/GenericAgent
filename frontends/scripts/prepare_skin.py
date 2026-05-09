#!/usr/bin/env python3
"""Prepare skin from source videos (mp4) → sprite sheet PNG + skin.json.

Usage:
    python3 prepare_skin.py <source_dir> <output_skin_dir> [--fps N] [--size WxH] [--chroma-key]

Example:
    python3 prepare_skin.py /path/to/leo_source ../skins/leo_hd --fps 18 --size 120x140 --chroma-key

Mapping: source videos are matched to animation states by index (图1→图9).
Default mapping: 图1=walk, 图2=idle, 图3=surprise, 图4=angry, 图5=bow,
                 图6=sad, 图7=jump, 图8=fly, 图9=happy

Background removal uses flood-fill from image edges: only removes background
pixels that are connected to the border, preserving same-color pixels inside
the character (e.g. green eyes or decorations on a green-background video).
"""
import os
import sys
import json
import subprocess
import tempfile
from pathlib import Path
from collections import deque
from PIL import Image

# Default index-to-state mapping (1-indexed, matching 图1-图9)
DEFAULT_MAPPING = {
    1: 'walk',
    2: 'idle',
    3: 'surprise',
    4: 'angry',
    5: 'bow',
    6: 'sad',
    7: 'jump',
    8: 'fly',
    9: 'happy',
}

LOOP_STATES = {'idle', 'walk', 'run', 'sprint', 'fly'}


def extract_index(filename: str) -> int | None:
    """Extract 图N index from filename like '...#洛克王国 #-图3.mp4'."""
    import re
    m = re.search(r'图(\d+)', filename)
    return int(m.group(1)) if m else None


def get_video_info(video_path: str) -> dict:
    """Get video metadata via ffprobe."""
    cmd = [
        'ffprobe', '-v', 'quiet', '-print_format', 'json',
        '-show_format', '-show_streams', video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    video_stream = next(s for s in data['streams'] if s['codec_type'] == 'video')
    return {
        'width': video_stream['width'],
        'height': video_stream['height'],
        'fps': eval(video_stream['r_frame_rate']),  # e.g. "30/1" → 30.0
        'duration': float(data['format']['duration']),
        'frame_count': int(video_stream.get('nb_frames', 0)),
    }


def extract_frames(video_path: str, fps: int, size: tuple[int, int],
                   chroma_key: bool = False) -> list[Image.Image]:
    """Extract frames from video as RGBA PIL Images."""
    w, h = size
    with tempfile.TemporaryDirectory() as tmpdir:
        # Extract frames as PNG
        pattern = os.path.join(tmpdir, 'frame_%05d.png')
        cmd = [
            'ffmpeg', '-y', '-i', video_path,
            '-vf', f'fps={fps},scale={w}:{h}',
            pattern
        ]
        subprocess.run(cmd, capture_output=True, check=True)

        # Load frames
        frames = []
        for fname in sorted(os.listdir(tmpdir)):
            if fname.endswith('.png'):
                img = Image.open(os.path.join(tmpdir, fname)).convert('RGBA')
                if chroma_key:
                    img = remove_background_flood(img)
                frames.append(img)

    return frames


def is_bg_pixel(r: int, g: int, b: int, tol: int = 60) -> bool:
    """Check if pixel matches the teal background color range.

    Background is approximately R<100, G>130, B>130 (teal/cyan).
    Tolerance controls how much G-B difference is allowed.
    """
    return r < 100 and g > 130 and b > 130 and abs(int(g) - int(b)) < tol


def remove_background_flood(img: Image.Image) -> Image.Image:
    """Remove background using flood-fill from image edges.

    Only removes background-colored pixels that are spatially connected
    to the image border. This preserves same-colored pixels inside the
    character (e.g. green eyes on a green background).
    """
    w, h = img.size
    data = img.load()
    visited = bytearray(w * h)  # flat array, faster than 2D list
    queue = deque()

    def idx(x, y):
        return y * w + x

    # Seed from all 4 edges
    for x in range(w):
        for y in (0, h - 1):
            if is_bg_pixel(*data[x, y][:3]) and not visited[idx(x, y)]:
                visited[idx(x, y)] = 1
                queue.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if is_bg_pixel(*data[x, y][:3]) and not visited[idx(x, y)]:
                visited[idx(x, y)] = 1
                queue.append((x, y))

    # BFS flood fill
    while queue:
        x, y = queue.popleft()
        r, g, b, a = data[x, y]
        data[x, y] = (r, g, b, 0)  # make transparent
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h:
                ni = idx(nx, ny)
                if not visited[ni] and is_bg_pixel(*data[nx, ny][:3]):
                    visited[ni] = 1
                    queue.append((nx, ny))

    return img


def create_sprite_sheet(frames: list[Image.Image], frame_width: int, frame_height: int) -> Image.Image:
    """Concatenate frames into a single-row sprite sheet.

    Each frame is center-aligned by centroid (center of mass of opaque pixels)
    so the character doesn't jitter between frames.
    """
    count = len(frames)
    sheet = Image.new('RGBA', (frame_width * count, frame_height))

    # Compute centroid x for each frame
    centroids = []
    crops = []
    for frame in frames:
        bbox = frame.getbbox()
        if bbox:
            char = frame.crop(bbox)
            # Compute horizontal centroid (center of mass of opaque pixels)
            data = char.load()
            cw, ch = char.size
            total_weight = 0
            weighted_x = 0
            for y in range(ch):
                for x in range(cw):
                    a = data[x, y][3]
                    total_weight += a
                    weighted_x += x * a
            if total_weight > 0:
                centroid_x = weighted_x / total_weight
            else:
                centroid_x = cw / 2
            centroids.append(centroid_x)
            crops.append((char, bbox))
        else:
            centroids.append(0)
            crops.append(None)

    # Use the median centroid as the anchor point
    valid_centroids = [c for c in centroids if c != 0]
    if not valid_centroids:
        for i, frame in enumerate(frames):
            sheet.paste(frame, (i * frame_width, 0))
        return sheet

    anchor = sorted(valid_centroids)[len(valid_centroids) // 2]

    for i, (crop_data) in enumerate(crops):
        if crop_data is None:
            continue
        char, bbox = crop_data
        cw, ch = char.size
        # Align centroid to anchor position, anchored to bottom
        x_offset = int(frame_width / 2 - centroids[i] + (cw - cw) // 2)
        # Recalculate: place char so its centroid lands at frame_width/2
        x_offset = int(frame_width / 2 - centroids[i])
        y_offset = frame_height - ch  # anchor to bottom
        aligned = Image.new('RGBA', (frame_width, frame_height))
        aligned.paste(char, (x_offset, y_offset))
        sheet.paste(aligned, (i * frame_width, 0))

    return sheet


def prepare_skin(source_dir: str, output_dir: str, fps: int = 18,
                 size: tuple[int, int] = (120, 140), chroma_key: bool = False,
                 mapping: dict | None = None):
    """Convert source videos to sprite sheet skin."""
    if mapping is None:
        mapping = DEFAULT_MAPPING

    os.makedirs(output_dir, exist_ok=True)

    # Find mp4 files
    mp4_files = sorted(f for f in os.listdir(source_dir) if f.endswith('.mp4'))
    if not mp4_files:
        print(f'No mp4 files found in {source_dir}')
        sys.exit(1)

    animations = {}
    skin_name = Path(output_dir).name

    for mp4_file in mp4_files:
        idx = extract_index(mp4_file)
        if idx is None or idx not in mapping:
            print(f'  Skipping {mp4_file} (no mapping for index {idx})')
            continue

        state_name = mapping[idx]
        video_path = os.path.join(source_dir, mp4_file)
        print(f'  Processing {mp4_file} → {state_name}')

        info = get_video_info(video_path)
        print(f'    Source: {info["width"]}x{info["height"]}, {info["fps"]}fps, {info["duration"]:.1f}s')

        frames = extract_frames(video_path, fps, size, chroma_key)
        if not frames:
            print(f'    WARNING: No frames extracted, skipping')
            continue

        frame_count = len(frames)
        # Slow down short animations (< 1.5s) by reducing fps instead of duplicating frames
        anim_fps = fps  # don't modify the loop variable
        min_duration = 1.5  # seconds
        actual_duration = frame_count / anim_fps
        if actual_duration < min_duration and frame_count > 0:
            anim_fps = max(3, int(frame_count / min_duration))
            print(f'    Slowed {state_name}: fps adjusted to {anim_fps} for {min_duration}s min duration')

        print(f'    Extracted {frame_count} frames @ {anim_fps}fps = {frame_count/anim_fps:.1f}s')

        # Create sprite sheet
        sheet = create_sprite_sheet(frames, size[0], size[1])
        sheet_file = f'{state_name}.png'
        sheet_path = os.path.join(output_dir, sheet_file)
        sheet.save(sheet_path)
        print(f'    Saved {sheet_file} ({sheet.size[0]}x{sheet.size[1]})')

        animations[state_name] = {
            'file': sheet_file,
            'loop': state_name in LOOP_STATES,
            'sprite': {
                'frameWidth': size[0],
                'frameHeight': size[1],
                'frameCount': frame_count,
                'columns': frame_count,
                'fps': anim_fps,
                'startFrame': 0,
            }
        }

    # Generate skin.json
    skin_config = {
        'name': skin_name,
        'version': '1.0',
        'size': {'width': size[0], 'height': size[1]},
        'animations': animations,
    }
    config_path = os.path.join(output_dir, 'skin.json')
    with open(config_path, 'w') as f:
        json.dump(skin_config, f, indent=2)
    print(f'\n  Generated skin.json with {len(animations)} animations')


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Prepare skin from source videos')
    parser.add_argument('source_dir', help='Directory containing mp4 source videos')
    parser.add_argument('output_dir', help='Output skin directory')
    parser.add_argument('--fps', type=int, default=18, help='Target FPS (default: 18)')
    parser.add_argument('--size', default='120x140', help='Frame size WxH (default: 120x140)')
    parser.add_argument('--chroma-key', action='store_true',
                        help='Remove teal/green background via flood-fill from edges (preserves character-internal same-color pixels)')
    args = parser.parse_args()

    w, h = map(int, args.size.split('x'))
    prepare_skin(args.source_dir, args.output_dir, fps=args.fps,
                 size=(w, h), chroma_key=args.chroma_key)


if __name__ == '__main__':
    main()
