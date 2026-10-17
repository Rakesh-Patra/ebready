"""Capture the real deployed UI and print the DevOps guide with release evidence."""
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import re
import shutil

from playwright.sync_api import sync_playwright
import markdown


def main():
    root = Path('artifacts')
    receipts = list(root.glob('release-*.json'))
    receipt = json.loads(max(receipts, key=lambda path: path.stat().st_mtime).read_text())
    if not receipt.get('verified'):
        raise SystemExit('Screenshots require a verified release receipt.')
    output = root / 'devops-document'; output.mkdir(exist_ok=True)
    shots = []
    with sync_playwright() as playwright:
        executable = os.environ.get('EBKIT_CHROMIUM_EXECUTABLE')
        browser = playwright.chromium.launch(**({'executable_path': executable} if executable else {}))
        page = browser.new_page(viewport={'width': 1440, 'height': 1000}, device_scale_factor=1)
        page.goto(receipt['url'], wait_until='networkidle')
        page.get_by_role('button', name='Explore demo', exact=True).wait_for()
        page.screenshot(path=str(output / '01-live-application.png'), full_page=True)
        shots.append(('01-live-application.png', 'Deployed application landing page'))
        page.get_by_role('button', name='Explore demo', exact=True).click()
        page.get_by_role('button', name='New deployment', exact=True).wait_for()
        page.screenshot(path=str(output / '02-deployed-dashboard.png'), full_page=True)
        shots.append(('02-deployed-dashboard.png', 'Guest dashboard served by the verified AWS release'))
        page.set_viewport_size({'width': 390, 'height': 844})
        page.screenshot(path=str(output / '03-mobile-dashboard.png'), full_page=True)
        shots.append(('03-mobile-dashboard.png', 'Deployed dashboard at phone width'))
        page.goto(receipt['url'] + '/api/health', wait_until='networkidle')
        page.set_viewport_size({'width': 1000, 'height': 400})
        page.screenshot(path=str(output / '04-health-response.png'), full_page=True)
        shots.append(('04-health-response.png', 'Health response from the deployed API'))
        assert page.request.get(receipt['url'] + '/insforge/api/auth/sessions').status == 403
        if os.environ.get('EBKIT_CAPTURE_GITHUB') == 'true':
            page.set_viewport_size({'width': 1440, 'height': 1000})
            response = page.goto(receipt['runUrl'], wait_until='networkidle')
            if not response or response.status != 200:
                raise RuntimeError('The public workflow page could not be captured.')
            page.screenshot(path=str(output / '05-github-actions.png'), full_page=True)
            shots.append(('05-github-actions.png', 'Actual public GitHub Actions run; see the linked run for individual steps'))
        dast_report = root / 'dast/zap.html'
        if dast_report.exists():
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.goto(dast_report.resolve().as_uri(), wait_until='load')
            page.screenshot(path=str(output / '06-passive-dast-report.png'))
            shots.append(('06-passive-dast-report.png', 'Actual ZAP report for the isolated CI container; warnings require review'))
        guide = Path('docs/DEVOPS-HACKATHON-GUIDE.md').read_text(encoding='utf-8')
        security_guide = Path('docs/DEVSECOPS.md')
        if security_guide.exists():
            guide += '\n\n' + security_guide.read_text(encoding='utf-8')
        content = markdown.markdown(guide, extensions=['fenced_code'])
        architecture = Path('docs/architecture.svg')
        if architecture.exists():
            shutil.copyfile(architecture, output / 'architecture.svg')
            content = re.sub(r'<pre><code class="language-mermaid">.*?</code></pre>',
                             '<figure><img src="architecture.svg" alt="Secure delivery architecture"><figcaption>Implementation architecture diagram</figcaption></figure>',
                             content, flags=re.DOTALL)
        # The architecture is readable as text when no Mermaid renderer is available.
        evidence = '<h2>Verified release evidence</h2><p>Capture time ' + html.escape(datetime.now(timezone.utc).isoformat()) + '</p>'
        evidence += '<pre>' + html.escape(json.dumps(receipt, indent=2)) + '</pre>'
        for filename, caption in shots:
            evidence += f'<figure><img src="{filename}" alt="{html.escape(caption)}"><figcaption>{html.escape(caption)}</figcaption></figure>'
        document = '''<!doctype html><html><head><meta charset="utf-8"><title>EBKit DevOps implementation and deployment</title><style>
        @page{size:Letter;margin:20mm 18mm}body{font:11pt/1.5 Arial,sans-serif;color:#16251d;max-width:820px;margin:40px auto}h1{font-size:26pt;line-height:1.15;color:#111}h1:not(:first-child){break-before:page}h2{font-size:16pt;margin-top:26px;break-after:avoid}h3{break-after:avoid}p,li{orphans:3;widows:3}pre{font:8.5pt/1.45 monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f4;padding:12px;border:1px solid #dbe2dd;break-inside:auto}code{font-size:.88em}a{color:#17684b;overflow-wrap:anywhere}figure{margin:22px 0;break-inside:avoid}img{display:block;max-width:100%;max-height:720px;object-fit:contain;margin:auto}figcaption{font-size:9pt;color:#53645a;margin-top:8px}ul,ol{padding-left:22px}@media print{body{margin:0;max-width:none}}
        </style></head><body>''' + content + evidence + '</body></html>'
        html_path = output / 'EBKit-DevOps-Guide.html'
        html_path.write_text(document, encoding='utf-8')
        page.goto(html_path.resolve().as_uri(), wait_until='load')
        page.pdf(path=str(output / 'EBKit-DevOps-Guide.pdf'), format='Letter', print_background=True,
                 display_header_footer=True, header_template='<div></div>',
                 footer_template='<div style="font:9px Arial;width:100%;text-align:center;color:#666">EBKit DevOps walkthrough · <span class="pageNumber"></span> / <span class="totalPages"></span></div>',
                 margin={'top':'20mm','bottom':'20mm','left':'18mm','right':'18mm'})
        browser.close()
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(output / 'EBKit-DevOps-Guide.pdf'))
    previews = output / 'qa'; previews.mkdir(exist_ok=True)
    for index in range(len(pdf)):
        pdf[index].render(scale=1.2).to_pil().save(previews / f'page-{index+1}.png')
    print(f'Captured {len(shots)} real screenshots and a {len(pdf)} page PDF. Review all QA page images before sharing.')


if __name__ == '__main__':
    main()
