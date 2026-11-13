from pathlib import Path
import shutil
import subprocess


class PdfViewer:
    def command(self, path, page: int) -> tuple[list[str], bool]:
        if path is None or page < 1:
            raise ValueError('A PDF path and a positive page number are required.')
        path = Path(path).expanduser().resolve(strict=True)
        if not path.is_file() or path.suffix.lower() != '.pdf':
            raise ValueError('Choose an existing PDF file.')
        for name, flag, number in [('evince', '--page-index', page - 1), ('atril', '--page-index', page - 1), ('okular', '--page', page)]:
            executable = shutil.which(name)
            if executable:
                return [executable, flag, str(number), str(path)], True
        executable = shutil.which('xdg-open')
        if executable is None:
            raise ValueError('Install a PDF viewer such as evince, or install xdg-utils.')
        return [executable, str(path)], False

    def open(self, path, page: int) -> str:
        command, page_supported = self.command(path, page)
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
        return f'Opened PDF at page {page}.' if page_supported else f'Opened PDF with the default viewer. Navigate to page {page}; this viewer has no configured page command.'
