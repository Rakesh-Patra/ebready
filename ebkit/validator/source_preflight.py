"""Expose the same source-only Scout validation used by local init."""
import argparse
import json
from dataclasses import asdict
from pathlib import Path
from ebkit.validator.docker_validator import DockerScoutValidator, ScoutResult

def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument('--path',required=True)
    options=parser.parse_args()
    root=Path(options.path).resolve()
    validator=DockerScoutValidator(max_critical=0,max_high=5)
    available=validator.is_scout_available()
    result=validator.scan_source(root) if available else ScoutResult(gate_passed=False,artifact_kind='source',summary='Docker Scout is unavailable. Source security validation: NOT VERIFIED.',gate_reason='Install and configure Docker Scout before deploying, or explicitly acknowledge a security bypass.')
    payload=asdict(result)
    payload.pop('raw_output',None)
    # Reports expose project-relative paths, not the operator's filesystem layout.
    for finding in payload['details']:
        finding['paths']=[str(value).replace(str(root),'').replace(root.as_posix(),'').lstrip('/\\') for value in finding.get('paths',[])]
    payload['available']=available
    print(json.dumps(payload))

if __name__=='__main__':
    main()
