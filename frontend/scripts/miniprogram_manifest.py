"""Shared page inventory and normal-subpackage dependency checks for builders."""
import json
import posixpath
import re


def page_inventory(app):
    """Return unchanged full routes; tab pages must stay in the main package."""
    def path(value):
        if (not isinstance(value, str) or not value or value.startswith('/')
                or '\\' in value or posixpath.normpath(value) != value
                or any(part in ('.', '..') for part in value.split('/'))):
            raise ValueError('Invalid Mini page or subpackage path')
        return value

    main = [path(page) for page in app.get('pages', [])]
    pages = list(main)
    roots = []
    for package in app.get('subPackages', app.get('subpackages', [])):
        root = path(package['root'])
        if package.get('independent'):
            raise ValueError('Independent packages require a separate dependency audit')
        if any(root == other or root.startswith(other + '/') or other.startswith(root + '/') for other in roots):
            raise ValueError('Overlapping Mini subpackage roots')
        roots.append(root)
        pages.extend(root + '/' + path(page) for page in package['pages'])
    if not main or len(pages) != len(set(pages)):
        raise ValueError('Missing main page or duplicate Mini page route')
    if any(page.startswith(root + '/') for page in main for root in roots):
        raise ValueError('Main page cannot belong to a subpackage')
    if any(tab['pagePath'] not in main for tab in app.get('tabBar', {}).get('list', [])):
        raise ValueError('Mini tab page must remain in the main package')
    return pages, roots


def validate_dependencies(files, roots):
    """Check static runtime imports; page navigation URLs are not code imports."""
    def owner(name):
        return next((root for root in roots if name.startswith(root + '/')), None)

    def check(source, reference, suffixes=('',)):
        if reference.startswith(('plugin://', 'http://', 'https://')):
            return
        target = posixpath.normpath(reference.lstrip('/') if reference.startswith('/')
                                   else posixpath.join(posixpath.dirname(source), reference))
        candidates = [target + suffix for suffix in suffixes]
        existing = [name for name in candidates if name in files]
        if not existing:
            raise ValueError(f'Missing Mini dependency: {source} -> {reference}')
        for name in existing:
            if owner(name) and owner(source) != owner(name):
                raise ValueError(f'Cross-subpackage dependency: {source} -> {name}')

    for name, content in files.items():
        suffix = posixpath.splitext(name)[1]
        if suffix not in ('.js', '.wxs', '.wxss', '.wxml', '.json'):
            continue
        text = content.decode('utf-8') if isinstance(content, bytes) else content
        if suffix in ('.js', '.wxs'):
            for ref in re.findall(r'\brequire\(\s*[\"\']([^\"\']+)[\"\']\s*\)', text):
                check(name, ref, ('', '.js', '/index.js'))
        elif suffix == '.wxss':
            # Comments often contain examples, rather than actual imports.
            text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
            for ref in re.findall(r'@import\s+[\"\']([^\"\']+)', text):
                check(name, ref)
        elif suffix == '.wxml':
            for ref in re.findall(r'<(?:import|include|wxs)\b[^>]*\bsrc=[\"\']([^\"\']+)', text):
                check(name, ref)
        elif suffix == '.json':
            for ref in json.loads(text).get('usingComponents', {}).values():
                check(name, ref, ('.js',))
