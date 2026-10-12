"""Use the existing Gordon integration for a text-only hosted init draft."""
import json
import sys
from ebkit.generator.docker_ai import DockerAIService

def generate(prompt: str) -> dict:
    service = DockerAIService()
    if not service.is_available():
        return {'available': False, 'dockerfile': None}
    try:
        # No project directory or tools: metadata/manifests are already in the prompt.
        text = service.ask_gordon(prompt, timeout=60)
        dockerfile = service.extract_dockerfile(text)
        return {'available': True, 'dockerfile': dockerfile}
    except Exception:
        return {'available': True, 'dockerfile': None}

def main() -> None:
    payload = json.loads(sys.stdin.read(3000000))
    prompt = payload.get('prompt')
    if not isinstance(prompt, str) or len(prompt) > 1000000:
        raise ValueError('Invalid generation prompt.')
    print(json.dumps(generate(prompt)))

if __name__ == '__main__':
    main()
