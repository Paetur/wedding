"""Download a pinned public release, verify it, and prepare a Pages-only artifact."""
import argparse, base64, hashlib, json, re, shutil, stat, tempfile, urllib.parse, urllib.request, zipfile
from pathlib import Path, PurePosixPath
MAX_DOWNLOAD = 32 * 1024 * 1024
MAX_OPENED = 128 * 1024 * 1024
# The separate Shortcuts test website copies these from source.
SHORTCUTS_PILOT_SCRIPTS = {
    'js/shortcut-device-runner.js',
    'js/shortcuts-test-installer.js',
    'js/shortcuts-test-page.js',
}

def checked_path(value):
    if value == '':
        return value
    if not isinstance(value, str) or not re.fullmatch('[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*', value):
        raise ValueError('Use an empty path for the site root, or a relative path without dots or a leading slash')
    return value

def connection(url, key):
    if not url and (not key):
        return {'supabaseUrl': '', 'supabaseKey': ''}
    if not url or not key:
        raise ValueError('Set both RSVP_SUPABASE_URL and RSVP_PUBLISHABLE_KEY, or leave both empty for setup')
    p = urllib.parse.urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or (p.path not in ('', '/')) or p.query or p.fragment:
        raise ValueError('Use the HTTPS public project URL without credentials, query or path')
    if key.startswith('sb_publishable_') and re.fullmatch('sb_publishable_[A-Za-z0-9_-]+', key):
        pass
    else:
        try:
            parts = key.split('.')
            assert len(parts) == 3
            claims = json.loads(base64.urlsafe_b64decode(parts[1] + '=' * (-len(parts[1]) % 4)))
            assert claims.get('role') == 'anon'
        except Exception:
            raise ValueError('Use only a public publishable key or anon JWT') from None
    return {'supabaseUrl': urllib.parse.urlunsplit((p.scheme, p.netloc, '', '', '')), 'supabaseKey': key}

def download(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'RSVP-Pages-deployment', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise ValueError('Release download exceeds the size limit')
    return data

def verify_full_ci(repository, reference):
    """Require successful full jobs for the exact public release-tag commit."""
    if not re.fullmatch('[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid release repository')
    api = 'https://api.github.com/repos/' + repository
    commit = json.loads(download(api + '/commits/' + urllib.parse.quote(reference, safe='')))['sha']
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('Invalid release commit')
    query = urllib.parse.urlencode({'head_sha': commit, 'status': 'success', 'per_page': 30})
    runs = json.loads(download(api + '/actions/workflows/rsvp-security-smoke.yml/runs?' + query))['workflow_runs']
    required = {'household-database', 'guest-feedback-concurrency', 'browser-smoke'}
    for run in runs:
        if run.get('head_sha') != commit or run.get('status') != 'completed' or run.get('conclusion') != 'success':
            continue
        identifier = run.get('id')
        if not isinstance(identifier, int):
            continue
        job_response = json.loads(download(api + '/actions/runs/' + str(identifier) + '/jobs?per_page=100'))
        jobs = job_response['jobs']
        if job_response.get('total_count', len(jobs)) != len(jobs):
            continue
        if required <= {job.get('name') for job in jobs} and all(job.get('status') == 'completed' and job.get('conclusion') == 'success' for job in jobs):
            return {'commit': commit, 'runURL': 'https://github.com/' + repository + '/actions/runs/' + str(identifier)}
    raise ValueError('Full CI has not passed for this release commit. Run rsvp-security-smoke.yml in the release repository and wait for all three jobs before hosted deployment. Quick checks, skipped jobs and other commits do not satisfy this gate.')

def fetch_release(repository, tag, directory):
    if not re.fullmatch('[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid release repository')
    if not re.fullmatch('v\\d+\\.\\d+\\.\\d+(?:-(?:alpha|beta|rc)\\.\\d+)?', tag):
        raise ValueError('Choose an explicit version tag')
    release = json.loads(download('https://api.github.com/repos/' + repository + '/releases/tags/' + tag))
    if release.get('draft') or release.get('tag_name') != tag:
        raise ValueError('Choose a published release matching the version tag')
    verification = verify_full_ci(repository, tag)
    (directory / 'full-ci.json').write_text(json.dumps(verification, indent=2) + '\n')
    filename = 'rsvp-manager-' + tag[1:] + '.zip'
    assets = {a['name']: a['browser_download_url'] for a in release['assets']}
    digest = download(assets[filename + '.sha256']).decode().split()[0]
    if not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Invalid published checksum')
    data = download(assets[filename])
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('Release ZIP checksum mismatch')
    archive = directory / filename
    archive.write_bytes(data)
    return archive

def unpack_verified(archive, destination, version):
    prefix = 'rsvp-manager-' + version + '/'
    with zipfile.ZipFile(archive) as z:
        entries = z.infolist()
        names = set()
        total = 0
        for item in entries:
            name = item.filename
            if name in names or not name.startswith(prefix) or '\\' in name:
                raise ValueError('Invalid or duplicate archive path')
            names.add(name)
            relative = name[len(prefix):]
            parts = PurePosixPath(relative)
            if not relative or relative.startswith('/') or any((p in ('..', '.') for p in parts.parts)) or (not parts.parts):
                raise ValueError('Archive path escapes its release root')
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError('Archive symlinks are not allowed')
            total += item.file_size
            if total > MAX_OPENED:
                raise ValueError('Opened release exceeds the size limit')
            if item.is_dir():
                continue
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(z.read(item))
    manifest = json.loads((destination / 'release-manifest.json').read_text())
    if manifest['version'] != version:
        raise ValueError('Release manifest version mismatch')
    expected = set(manifest['files']) | {'release-manifest.json', 'SHA256SUMS'}
    actual = {p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()}
    if actual != expected:
        raise ValueError('Release file list does not match its manifest')
    for name, digest in manifest['files'].items():
        if name.startswith('/') or '..' in PurePosixPath(name).parts or hashlib.sha256((destination / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Release file checksum mismatch')
    return manifest

def build_site(archive, output, version, site_path, api_url='', public_key='', verification=None):
    site_path = checked_path(site_path)
    cfg = connection(api_url, public_key)
    if output.exists():
        raise ValueError('Choose a fresh site output directory')
    with tempfile.TemporaryDirectory(prefix='rsvp-pages-') as temporary:
        source = Path(temporary)
        manifest = unpack_verified(archive, source, version)
        target = output / site_path
        target.mkdir(parents=True)
        selected = []
        for path in sorted(source.rglob('*')):
            if not path.is_file():
                continue
            name = path.relative_to(source).as_posix()
            if name in SHORTCUTS_PILOT_SCRIPTS:
                continue
            if name in ['index.html', 'setup.html', 'setup-automatic.html', 'version.json', 'supabase/install.sql', 'supabase/install-manifest.json'] or any((name.startswith(d + '/') for d in ['admin', 'css', 'js', 'locales', 'docs', 'supabase/studio'])):
                selected.append(name)
                dest = target / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, dest)
        for required in ['index.html', 'setup.html', 'js/core.js', 'admin/index.html', 'supabase/install.sql']:
            if required not in selected:
                raise ValueError('Website release is missing ' + required)
        (target / 'js/config.js').write_text('// Per-installation PUBLIC browser configuration; generated by deployment.\nwindow.RSVP_CONFIG = Object.freeze(' + json.dumps(cfg) + ');\n')
        info = {'version': version, 'releaseSourceCommit': manifest['sourceCommit'], 'path': '/' + site_path + '/' if site_path else '/', 'backendConfigured': bool(cfg['supabaseUrl'])}
        if verification:
            info['fullCI'] = verification
        (target / 'deployment-state.json').write_text(json.dumps(info, indent=2) + '\n')
        hashes = {p.relative_to(target).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(target.rglob('*')) if p.is_file()}
        (target / 'deployment-manifest.json').write_text(json.dumps({'version': version, 'files': hashes}, indent=2) + '\n')
        (output / '.nojekyll').write_text('')
        if site_path:
            (output / 'index.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>RSVP Manager</title><main><h1>RSVP Manager</h1><p><a href="' + site_path + '/">Open invitation</a></p></main></html>')
        return info

def main():
    import os
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('deployment.json'))
    parser.add_argument('--tag')
    parser.add_argument('--output', type=Path, default=Path('_site'))
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    tag = args.tag or config['releaseTag']
    with tempfile.TemporaryDirectory(prefix='rsvp-download-') as temporary:
        archive = fetch_release(config['releaseRepository'], tag, Path(temporary))
        result = build_site(archive, args.output, tag[1:], config['path'], os.environ.get('RSVP_SUPABASE_URL', ''), os.environ.get('RSVP_PUBLISHABLE_KEY', ''), verification=json.loads((Path(temporary) / 'full-ci.json').read_text()))
    print(json.dumps(result))
    if (summary := os.environ.get('GITHUB_STEP_SUMMARY')):
        with open(summary, 'a') as f:
            f.write('## RSVP deployment\n\nVersion: `' + result['version'] + '`\n\nPath: `' + result['path'] + '`\n\nBackend: ' + ('configured' if result['backendConfigured'] else 'unconfigured; ready for setup') + '\n\nFull CI: ' + result['fullCI']['runURL'] + '\n\nThis workflow does not create, restore or publish an event.\n')
if __name__ == '__main__':
    main()
