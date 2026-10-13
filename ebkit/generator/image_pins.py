"""Resolve real registry digests; never invent pins or rewrite stage aliases."""
import re
import shlex
import subprocess


def pin_base_images(text: str) -> str:
    aliases, pins = set(), {}
    lines = text.splitlines(True)
    for index,line in enumerate(lines):
        match = re.match(r'(\s*FROM\s+)(.*)',line,re.I)
        if not match:
            continue
        tokens = shlex.split(match[2])
        values = [token for token in tokens if not token.startswith('--')]
        if not values:
            raise ValueError('Malformed FROM instruction.')
        image = values[0]
        reference=image.split('@sha256:',1)[0]
        immutable='@sha256:' in image and ':' not in reference.rsplit('/',1)[-1]
        if image not in aliases and image != 'scratch' and not immutable:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9./:_-]*',reference):
                raise ValueError('Resolve dynamic/invalid base image references before digest pinning.')
            if reference not in pins:
                try:
                    result = subprocess.run(['docker','buildx','imagetools','inspect',reference],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
                except (OSError,subprocess.TimeoutExpired) as error:
                    raise ValueError('Base digest resolution requires Docker Buildx and registry access.') from error
                digest = re.search(r'^Digest:\s+(sha256:[a-f0-9]{64})\s*$',result.stdout,re.M)
                if result.returncode or not digest:
                    raise ValueError(f'Could not resolve a real registry digest for {image}. No Dockerfile changes were applied.')
                pins[reference] = digest[1]
            lines[index] = re.sub(re.escape(image)+r'(?=\s|$)',reference+'@'+pins[reference],line,count=1)
        if len(values) >= 3 and values[-2].upper() == 'AS':
            aliases.add(values[-1])
    return ''.join(lines)
