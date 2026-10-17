"""Print installed runtime dependency versions without requiring pip in the image."""
from importlib.metadata import distributions

# EBKit is local source, checked by SAST; all installed third-party distributions
# are audited against their exact runtime versions in the public advisory database.
print('\n'.join(sorted(
    f"{item.metadata['Name']}=={item.version}"
    for item in distributions() if item.metadata['Name'].casefold() != 'ebkit'
)))
