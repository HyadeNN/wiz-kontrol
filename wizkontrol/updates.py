"""GitHub üzerinden yeni sürüm denetimi. Yalnızca bildirir, hiçbir şey indirmez veya kurmaz."""
import json
import logging
import re

from . import __version__

REPO_URL = "https://github.com/HyadeNN/wiz-kontrol"
_API_URL = "https://api.github.com/repos/HyadeNN/wiz-kontrol/releases/latest"

log = logging.getLogger(__name__)


def is_newer(tag, current=__version__):
    """"v1.2.0" gibi bir etiket mevcut sürümden yeni mi?"""
    parse = lambda v: [int(n) for n in re.findall(r"\d+", v)[:3]]
    return parse(tag) > parse(current)


def latest_release(timeout=5):
    """En son sürümün (etiket, sayfa adresi) çiftini döndürür; ulaşılamazsa None."""
    import urllib.request  # ssl belleği şişirir; yalnızca denetim yapılırken yüklensin

    request = urllib.request.Request(_API_URL, headers={"User-Agent": f"wiz-kontrol/{__version__}",
                                                        "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
        return data["tag_name"], data["html_url"]
    except (OSError, ValueError, KeyError) as e:
        log.info("Sürüm denetimi başarısız: %s", e)
        return None
