"""
Web Search Tool - DuckDuckGo search (no API key required).
"""

from typing import Dict, Any, List, Optional


def search_web(query: str, max_results: int = 10, region: str = 'wt-wt') -> Dict[str, Any]:
    """
    Execute a web search and return results.

    Args:
        query: Search query
        max_results: Max number of results (default 10)
        region: Region code (wt-wt = worldwide)

    Returns:
        Dict with success, results (list of {title, href, body}), or error
    """
    try:
        from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results, region=region))

        items = []
        for r in results:
            items.append({
                'title': r.get('title', ''),
                'href': r.get('href', ''),
                'body': r.get('body', ''),
            })

        return {
            'success': True,
            'query': query,
            'results': items,
            'count': len(items),
        }
    except ImportError:
        return {
            'success': False,
            'error': 'duckduckgo-search package not installed. Run: pip install duckduckgo-search',
            'results': [],
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'query': query,
            'results': [],
        }
