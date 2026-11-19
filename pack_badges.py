"""Print a private badge-template bundle for `wrangler secret put` on stdin."""

import argparse
import base64
import json
from pathlib import Path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    result = {}
    for name in ('front.png', 'dark.png', 'light.png'):
        image = (args.directory / name).read_bytes()
        if not 40 <= len(image) <= 12000 or not image.startswith(b'\x89PNG\r\n\x1a\n'):
            parser.error(f'{name} must be a small PNG badge crop.')
        result[name] = base64.b64encode(image).decode('ascii')
    print(json.dumps(result, separators=(',', ':')))
