ALLOWED_LICENSES = ['CC0', 'Public Domain', 'CC BY', 'CC BY-SA', 'CC BY 4.0', 'CC BY-SA 4.0', 'Unsplash License', 'Pixabay License', 'Pexels License']

REJECTED_LICENSE_PATTERNS = ['nc', 'nd', 'editorial', 'all rights reserved', 'getty', 'shutterstock']

NSFW_BLOCKLIST = [
    'nude', 'nudity', 'naked', 'nsfw', 'xxx', 'porn', 'pornograph', 'erotic', 'erotica',
    'sex', 'sexual', 'genital', 'penis', 'vagina', 'breast', 'nipple', 'topless',
    'underwear', 'lingerie', 'bra', 'panties', 'fetish', 'bondage', 'bdsm',
    'adult content', 'explicit', 'mature content', '18+', 'r-rated',
    'playboy', 'hustler', 'penthouse', 'onlyfans',
    'stripper', 'striptease', 'burlesque', 'provocative',
    'masturbat', 'orgasm', 'intercourse', 'coitus',
    'hentai', 'ecchi', 'yaoi', 'yuri',
    'stockings', 'garter', 'corset', 'thong', 'bikini model',
    'pin-up', 'pinup', 'glamour model', 'glamor model',
    'body paint', 'body-paint', 'implied nude'
]

WIKIMEDIA_ALLOWED_LICENSES = [
    'cc0', 'cc-zero', 'public domain', 'pd',
    'cc-by', 'cc-by-4.0', 'cc-by-3.0', 'cc-by-2.5',
    'cc-by-sa', 'cc-by-sa-4.0', 'cc-by-sa-3.0', 'cc-by-sa-2.5'
]


def is_nsfw_content(title, description='', categories=None):
    """Check if content appears to be NSFW based on title, description, and categories."""
    text_to_check = f"{title} {description} {' '.join(categories or [])}".lower()
    for term in NSFW_BLOCKLIST:
        if term in text_to_check:
            return True, f"Blocked: contains '{term}'"
    return False, None


def validate_license(license_short):
    """
    Validate a license string. Returns (is_valid, license_type, rejection_reason).
    CRITICAL: Check rejection patterns FIRST before allowing.
    """
    license_lower = license_short.lower().strip()
    
    for pattern in REJECTED_LICENSE_PATTERNS:
        if pattern in license_lower:
            return False, None, f'Rejected: contains "{pattern}"'
    
    if 'cc0' in license_lower or 'cc-zero' in license_lower or 'cc zero' in license_lower:
        return True, 'CC0', None
    if 'public domain' in license_lower or license_lower == 'pd' or 'pd-' in license_lower:
        return True, 'Public Domain', None
    if 'cc-by-sa' in license_lower or 'cc by-sa' in license_lower or 'ccbysa' in license_lower:
        return True, 'CC BY-SA', None
    if 'cc-by' in license_lower or 'cc by' in license_lower or 'ccby' in license_lower:
        return True, 'CC BY', None
    if 'pexels' in license_lower:
        return True, 'Pexels License', None
    if 'pixabay' in license_lower:
        return True, 'Pixabay License', None
    if 'unsplash' in license_lower:
        return True, 'Unsplash License', None
    if license_lower.startswith('cc ') or license_lower.startswith('cc-'):
        return True, 'Creative Commons', None
    if 'fal' in license_lower or 'free art' in license_lower:
        return True, 'FAL', None
    if 'gfdl' in license_lower or 'gnu free documentation' in license_lower:
        return True, 'GFDL', None
    
    if license_lower and len(license_lower) > 0:
        return True, license_short[:20], None
    
    return False, None, f'Empty license: {license_short}'
