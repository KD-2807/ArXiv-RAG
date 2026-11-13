import stat
from pathlib import Path
import json
import os
import re


class MountedSources:
    def __init__(self, mountinfo: Path = Path('/proc/self/mountinfo')) -> None:
        self.mountinfo = mountinfo

    @staticmethod
    def _unescape(value: str) -> str:
        return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), value)

    def _entries(self) -> list[dict[str, str]]:
        entries = []
        try:
            for line in self.mountinfo.read_text().splitlines():
                left, right = line.split(' - ', 1)
                fields, filesystem = left.split(), right.split()
                entries.append({
                    'mountpoint': self._unescape(fields[4]),
                    'device': fields[2], 'fsroot': self._unescape(fields[3]),
                    'fstype': filesystem[0], 'source': self._unescape(filesystem[1]),
                })
        except (OSError, ValueError, IndexError) as error:
            raise ValueError('Cannot inspect Linux mounted sources. Indexing and cleanup are refused.') from error
        return entries

    def capture(self, root: Path) -> str:
        entries = self._entries()
        ancestors = [entry for entry in entries if root.is_relative_to(entry['mountpoint'])]
        if not ancestors:
            raise ValueError('Cannot identify the filesystem containing the PDF root.')
        depth = max(len(Path(entry['mountpoint']).parts) for entry in ancestors)
        relevant = [entry for entry in entries if Path(entry['mountpoint']).is_relative_to(root)
                    or (entry in ancestors and len(Path(entry['mountpoint']).parts) == depth)]
        records: list[dict[str, str | int]] = []
        for entry in relevant:
            point = Path(entry['mountpoint'])
            try:
                details = point.stat()
                if point.is_dir():
                    with os.scandir(point):
                        pass
                else:
                    with point.open('rb'):
                        pass
            except OSError as error:
                raise ValueError(f'Mounted source is unavailable: {point}. Restore it before indexing or cleanup.') from error
            records.append({**entry, 'stat_device': details.st_dev, 'inode': details.st_ino})
        if self._entries() != entries:
            raise ValueError('Mount table changed during inspection. Retry when storage is stable.')
        return json.dumps(sorted(records, key=lambda item: json.dumps(item, sort_keys=True)), sort_keys=True)

    def validate(self, root: Path, expected: str) -> None:
        if self.capture(root) != expected:
            raise ValueError('Mounted sources changed or disappeared. Restore the original sources; indexing and cleanup are refused.')


def root_identity(path: Path) -> str:
    details = path.stat()
    if not stat.S_ISDIR(details.st_mode):
        raise ValueError('PDF root is not a directory.')
    with os.scandir(path):
        pass
    return json.dumps([details.st_dev, details.st_ino])
