"""
Web Interface for DocBridge Document Search

Flask-based web application for lawyers to search and access DocBridge documents
through Knovas.
"""

import sys
import os
import json
import hmac
import secrets
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for
from flask_cors import CORS
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
import subprocess
import platform
import re
from urllib.parse import quote

from config_loader import get_config
from knovas_client import KnovasAPIClient
from file_utils import AutoDocFileHandler
from open_tokens import OpenTokenManager
from unc_path import (
    filesystem_path_to_client_local,
    map_path_with_roots,
    normalize_client_local_root,
    normalize_local_root,
    normalize_unc_root,
    parse_unc_roots_list,
)

logger = logging.getLogger(__name__)


def _configure_logging_for_wsgi(config) -> None:
    """
    Gunicorn imports wsgi:app without running main(), so logging.basicConfig never runs.
    Ensure INFO logs (e.g. search similarity debug) reach docker logs (stderr).
    """
    level_name = (os.getenv('LOG_LEVEL') or config.get('logging.level') or 'INFO').upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(
            logging.Formatter('%(asctime)s %(levelname)s [%(name)s] %(message)s')
        )
        root.addHandler(h)
    for name in ('web_interface', 'web_interface.app', 'knovas_client'):
        logging.getLogger(name).setLevel(level)


_search_enrichment_cache: Dict[str, dict] = {}
_search_enrichment_mtime: float = 0.0


def _build_similarity_debug(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Structured payload for API / browser DevTools (after enrichment + refinement)."""
    rows = []
    scores: List[float] = []
    for r in results:
        s = float(r.get('score') or 0)
        scores.append(s)
        rows.append({
            'doc_id': r.get('doc_id'),
            'score': s,
            'title': r.get('title'),
            'page_number': r.get('page_number'),
            'sentence_number': r.get('sentence_number'),
            'cosine_similarity': r.get('cosine_similarity'),
            'cosine_distance': r.get('cosine_distance'),
        })
    return {
        'count': len(results),
        'max_score': max(scores) if scores else None,
        'min_score': min(scores) if scores else None,
        'results': rows,
    }


def _rel_path_for_autodoc(pointer: str) -> str:
    """
    Map Knovas pointer to a path under the autodoc mount.

    RemoteController sync uses identifier_prefix (e.g. ``corpus/rel/path.txt``).
    Set AUTODOC_IDENTIFIER_PREFIX=corpus when the mount root is the corpus folder itself.
    """
    rel = (pointer or "").strip().replace("\\", "/")
    prefix = (os.getenv("AUTODOC_IDENTIFIER_PREFIX") or "").strip().strip("/")
    if prefix and rel.startswith(prefix + "/"):
        rel = rel[len(prefix) + 1 :]
    return rel


def _log_search_similarity_debug(query: str, results: List[Dict[str, Any]]) -> None:
    """Docker logs: see whether Knovas scores survived into each hit."""
    if not results:
        logger.info("Search similarity debug query=%r: 0 results", query)
        return
    scores = [float(r.get('score') or 0) for r in results]
    brief = [
        {
            'doc_id': r.get('doc_id'),
            'score': float(r.get('score') or 0),
            'page': r.get('page_number'),
            'cos_sim': r.get('cosine_similarity'),
            'cos_dist': r.get('cosine_distance'),
        }
        for r in results
    ]
    logger.info(
        "Search similarity debug query=%r count=%s max_score=%.6f min_score=%.6f detail=%s",
        query,
        len(results),
        max(scores),
        min(scores),
        brief,
    )


def create_app(config_path: Optional[str] = None):
    """
    Create and configure Flask application.
    
    Args:
        config_path: Path to configuration file
        
    Returns:
        Configured Flask app
    """
    app = Flask(__name__)
    
    if config_path:
        from config_loader import ConfigLoader
        config = ConfigLoader(config_path)
    else:
        config = get_config()

    _configure_logging_for_wsgi(config)
    
    web_secret_key = str(config.get('web.secret_key', '') or '')
    app.config['SECRET_KEY'] = web_secret_key or 'change-me-in-production'
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(
        seconds=config.get_int('web.session_lifetime', 3600)
    )
    
    CORS(app)

    api_client = KnovasAPIClient(config)
    file_handler = AutoDocFileHandler()
    login_enabled = config.get_bool('web.login.enabled', True)
    web_app_title = str(config.get('web.app_title', 'Knovas Document Search') or 'Knovas Document Search')
    login_company_name = config.get('web.login.company_name', 'Knovas')
    login_username = str(config.get('web.login.username', '') or '')
    login_password = str(config.get('web.login.password', '') or '')
    login_configured = bool(login_username and login_password)
    weak_secret_values = {
        '',
        'change-me',
        'change-me-in-production',
        'replace-with-random-hex',
    }
    weak_password_values = {
        '',
        'change-me',
        'change-me-company-password',
        'replace-with-strong-company-password',
    }

    if login_enabled and not login_configured:
        logger.warning(
            "Company login is enabled but COMPANY_LOGIN_NAME or COMPANY_LOGIN_PASSWORD is missing."
        )
    if login_enabled and web_secret_key in weak_secret_values:
        raise RuntimeError('WEB_SECRET_KEY must be set to a strong random value when login is enabled.')
    if login_enabled and login_password in weak_password_values:
        raise RuntimeError('COMPANY_LOGIN_PASSWORD must be changed before login can be enabled.')

    open_section = config.get_dict('open', {}) or {}
    browser_client_open_enabled = config.get_bool('open.browser_client_path', True)
    companion_enabled = config.get_bool('open.companion_enabled', False)
    open_token_ttl = max(30, config.get_int('open.token_ttl_seconds', 120))
    open_token_manager = OpenTokenManager(
        str(app.config['SECRET_KEY']),
        max_age_seconds=open_token_ttl,
    )
    pdf_inline_in_browser = config.get_bool('open.pdf_inline_in_browser', True)
    allow_server_side_startfile = config.get_bool('open.allow_server_side_startfile', False)
    allow_degraded_download_open = config.get_bool('open.allow_degraded_download_open', False)
    companion_uri_scheme = str(open_section.get('companion_uri_scheme') or 'semantix-doc').strip()
    public_base_url_config = str(open_section.get('public_base_url') or '').strip().rstrip('/')

    client_local_root = normalize_client_local_root(str(open_section.get('client_local_root') or ''))

    def _open_unc_root_pairs() -> List[Tuple[str, str]]:
        roots = parse_unc_roots_list(open_section.get('unc_roots'))
        if not roots:
            loc = str(open_section.get('local_root') or '').strip()
            unc = normalize_unc_root(str(open_section.get('unc_root') or ''))
            if loc and unc:
                roots = [(loc, unc)]
        if not roots:
            unc_only = normalize_unc_root(str(open_section.get('unc_root') or ''))
            if unc_only:
                roots = [(os.path.abspath(file_handler.autodoc_path), unc_only)]
        return roots

    def _open_server_local_roots() -> List[str]:
        """Server/container paths used as the left side of UNC or client-local mapping."""
        seen: set[str] = set()
        out: List[str] = []
        for loc, _unc in _open_unc_root_pairs():
            norm = normalize_local_root(loc)
            if norm and norm not in seen:
                seen.add(norm)
                out.append(norm)
        explicit = normalize_local_root(str(open_section.get('local_root') or ''))
        if explicit and explicit not in seen:
            out.append(explicit)
        autodoc = normalize_local_root(str(file_handler.autodoc_path))
        if autodoc and autodoc not in seen:
            out.append(autodoc)
        return out

    def _open_mapping_configured() -> bool:
        if _open_unc_root_pairs():
            return True
        return bool(client_local_root and _open_server_local_roots())

    def _unc_for_resolved_path(full_path: str) -> Optional[str]:
        roots = _open_unc_root_pairs()
        if not roots:
            return None
        return map_path_with_roots(full_path, roots)

    def _client_path_for_resolved_path(full_path: str) -> Optional[str]:
        if not client_local_root:
            return None
        for loc in _open_server_local_roots():
            p = filesystem_path_to_client_local(full_path, loc, client_local_root)
            if p:
                return p
        return None

    def _can_open_via_companion(full_path: str) -> bool:
        return bool(_unc_for_resolved_path(full_path) or _client_path_for_resolved_path(full_path))

    def _client_open_targets(full_path: str) -> Dict[str, str]:
        """Client-visible paths the user's OS can open (UNC on Windows, mount path on Linux)."""
        out: Dict[str, str] = {}
        unc = _unc_for_resolved_path(full_path)
        if unc:
            out['unc'] = unc
        client_path = _client_path_for_resolved_path(full_path)
        if client_path:
            out['path'] = client_path
        return out

    def _is_safe_next(target: Optional[str]) -> bool:
        """Allow only local redirects after login."""
        return bool(
            target
            and target.startswith('/')
            and not target.startswith('//')
            and not target.startswith('/\\')
            and '\\' not in target
        )

    def _ensure_csrf_token() -> str:
        token = session.get('csrf_token')
        if not token:
            token = secrets.token_urlsafe(32)
            session['csrf_token'] = token
        return token

    def _csrf_token_is_valid(submitted_token: str) -> bool:
        token = session.get('csrf_token')
        if not token or not submitted_token:
            return False
        return hmac.compare_digest(str(token).encode('utf-8'), submitted_token.encode('utf-8'))

    def _login_redirect():
        next_url = request.full_path if request.query_string else request.path
        return redirect(url_for('login', next=next_url))

    def _credentials_match(submitted_name: str, submitted_password: str) -> bool:
        expected_name = login_username.encode('utf-8')
        expected_password = login_password.encode('utf-8')
        actual_name = submitted_name.encode('utf-8')
        actual_password = submitted_password.encode('utf-8')
        name_ok = hmac.compare_digest(actual_name, expected_name)
        password_ok = hmac.compare_digest(actual_password, expected_password)
        return name_ok and password_ok

    def _resolve_autodoc_path(file_path: str) -> Optional[str]:
        rel = _rel_path_for_autodoc(file_path)
        if os.path.isabs(rel):
            return None
        base_path = os.path.abspath(file_handler.autodoc_path)
        candidate = os.path.abspath(os.path.join(base_path, rel))
        try:
            if os.path.commonpath([base_path, candidate]) != base_path:
                return None
        except ValueError:
            return None
        return candidate

    @app.before_request
    def require_company_login():
        """Require a shared company login before serving the search UI and APIs."""
        if not login_enabled:
            return None
        if request.endpoint in {
            'static',
            'login',
            'logout',
            'stats',
            'health',
            'open_token_redeem',
            'open_tokens_spec',
        }:
            return None
        if session.get('company_login_ok') is True:
            return None
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'error': 'Login erforderlich'}), 401
        return _login_redirect()

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        """Company login page."""
        if not login_enabled:
            return redirect(url_for('index'))

        next_url = request.args.get('next') or url_for('index')
        if not _is_safe_next(next_url):
            next_url = url_for('index')

        if session.get('company_login_ok') is True:
            return redirect(next_url)

        error = None
        csrf_token = _ensure_csrf_token()
        if request.method == 'POST':
            submitted_name = str(request.form.get('login_name', '') or '')
            submitted_password = str(request.form.get('password', '') or '')
            submitted_csrf = str(request.form.get('csrf_token', '') or '')
            next_url = request.form.get('next') or next_url
            if not _is_safe_next(next_url):
                next_url = url_for('index')

            if not _csrf_token_is_valid(submitted_csrf):
                error = 'Login-Formular ist abgelaufen. Bitte erneut versuchen.'
                csrf_token = _ensure_csrf_token()
            elif not login_configured:
                error = 'Login ist noch nicht konfiguriert. Bitte .env prüfen.'
            elif _credentials_match(submitted_name, submitted_password):
                session.clear()
                session.permanent = True
                session['company_login_ok'] = True
                session['company_login_name'] = submitted_name
                session['csrf_token'] = secrets.token_urlsafe(32)
                return redirect(next_url)
            else:
                error = 'Login-Name oder Passwort ist falsch.'

        return render_template(
            'login.html',
            app_title=web_app_title,
            company_name=login_company_name,
            error=error,
            next_url=next_url,
            csrf_token=csrf_token,
        )

    @app.route('/logout', methods=['POST'])
    def logout():
        """Clear the company login session."""
        submitted_csrf = str(request.form.get('csrf_token', '') or '')
        if not _csrf_token_is_valid(submitted_csrf):
            return jsonify({'success': False, 'error': 'CSRF token ungültig'}), 400
        session.clear()
        return redirect(url_for('login'))

    @app.route('/')
    def index():
        """Main search page."""
        return render_template(
            'index.html',
            app_title=web_app_title,
            company_name=login_company_name,
            csrf_token=_ensure_csrf_token(),
            companion_enabled=companion_enabled,
            browser_client_open_enabled=browser_client_open_enabled,
            allow_degraded_download_open=allow_degraded_download_open,
            pdf_inline_in_browser=pdf_inline_in_browser,
        )
    
    @app.route('/api/search', methods=['POST'])
    def search():
        """
        Search documents via Knovas API.
        
        Request JSON:
            {
                "query": "search query",
                "limit": 20,
                "filters": {}
            }
        
        Returns:
            JSON with search results
        """
        try:
            data = request.get_json()
            
            if not data or 'query' not in data:
                return jsonify({'error': 'Query parameter required'}), 400
            
            query = data['query']
            limit = data.get('limit', config.get_int('web.search.results_per_page', 20))
            filters = data.get('filters', {})
            
            logger.info(f"Search request: query='{query}', limit={limit}")

            min_qlen = config.get_int('web.search.min_query_length', 2)
            qstrip = (query or '').strip()
            if min_qlen > 1 and len(qstrip) < min_qlen:
                return jsonify({
                    'success': False,
                    'error': f'Suchbegriff muss mindestens {min_qlen} Zeichen haben.',
                }), 400

            results = api_client.search_documents(query=query, limit=limit, filters=filters)

            enhanced_results = _enhance_search_results(results, file_handler)
            for result in enhanced_results.get('results') or []:
                fp = (result.get('path') or '').strip()
                if fp and result.get('file_exists') and not result.get('external_url'):
                    full = _resolve_autodoc_path(fp)
                    if full:
                        targets = _client_open_targets(full)
                        if browser_client_open_enabled and targets:
                            result['open_via_browser'] = True
                            if targets.get('unc'):
                                result['client_open_unc'] = targets['unc']
                            if targets.get('path'):
                                result['client_open_path'] = targets['path']
                        if companion_enabled and _can_open_via_companion(full):
                            result['open_via_companion'] = True
                            result['companion_scheme'] = companion_uri_scheme
            refined = _apply_search_refinement(enhanced_results, query, filters, config)

            final_results = refined.get('results', [])
            final_results = _supplement_results_from_enrichment_filenames(
                query, final_results, filters, config
            )
            if config.get_bool('web.search.log_similarity_scores', True):
                _log_search_similarity_debug(query, final_results)

            payload: Dict[str, Any] = {
                'success': True,
                'query': query,
                'results': final_results,
                'total': len(final_results),
                'timestamp': datetime.now().isoformat(),
            }
            if 'semantix' in refined and isinstance(refined.get('semantix'), dict):
                payload['semantix'] = refined['semantix']
            if config.get_bool('web.search.expose_similarity_scores_in_json', False):
                payload['similarity_debug'] = _build_similarity_debug(final_results)

            return jsonify(payload)
            
        except Exception as e:
            logger.error(f"Search error: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/document/<doc_id>', methods=['GET'])
    def get_document(doc_id: str):
        """
        Get document metadata.
        
        Args:
            doc_id: Document ID
            
        Returns:
            JSON with document metadata
        """
        try:
            logger.info(f"Document request: doc_id={doc_id}")
            
            return jsonify({
                'success': True,
                'doc_id': doc_id,
                'message': 'Document metadata endpoint (to be implemented)'
            })
            
        except Exception as e:
            logger.error(f"Error retrieving document: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/document/<doc_id>/open', methods=['POST'])
    def open_document(doc_id: str):
        """
        Legacy: open on server host (os.startfile). Disabled when companion open is mandatory.

        Prefer POST /api/open-tokens/mint + Semantix Open Companion (UNC, no temp copy).
        """
        try:
            data = request.get_json() or {}
            file_path = data.get('path')

            if not file_path:
                return jsonify({
                    'success': False,
                    'error': 'Document path required'
                }), 400

            full_path = _resolve_autodoc_path(file_path)
            if not full_path:
                return jsonify({
                    'success': False,
                    'error': 'Document path not allowed'
                }), 400

            if not allow_server_side_startfile:
                hint = (
                    'Server-seitiges Öffnen ist deaktiviert. Dateien werden auf dem Client-PC geöffnet.'
                )
                if browser_client_open_enabled:
                    hint += ' Bitte „Öffnen“ in der Suchoberfläche verwenden (Browser-Client-Pfad).'
                elif companion_enabled:
                    hint += ' Bitte Companion-Open (POST /api/open-tokens/mint).'
                return jsonify({
                    'success': False,
                    'error': hint,
                    'use_browser_client_path': browser_client_open_enabled,
                    'use_companion': companion_enabled,
                }), 410

            if not os.path.exists(full_path):
                return jsonify({
                    'success': False,
                    'error': 'Document file not found'
                }), 404

            logger.info(f"Opening document (server-side): {full_path}")

            success = _open_file_external(full_path, config)

            if success:
                return jsonify({
                    'success': True,
                    'message': f'Document opened: {doc_id}'
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'Failed to open document'
                }), 500

        except Exception as e:
            logger.error(f"Error opening document: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/document/<doc_id>/download', methods=['GET'])
    def download_document(doc_id: str):
        """
        Download document file.
        
        Args:
            doc_id: Document ID
            
        Returns:
            File download response
        """
        try:
            file_path = request.args.get('path')
            
            if not file_path:
                return jsonify({'error': 'Document path required'}), 400
            
            full_path = _resolve_autodoc_path(file_path)
            if not full_path:
                return jsonify({'error': 'Document path not allowed'}), 400
            
            if not os.path.exists(full_path):
                return jsonify({'error': 'Document file not found'}), 404
            
            logger.info(f"Downloading document: {full_path}")
            
            return send_file(
                full_path,
                as_attachment=True,
                download_name=os.path.basename(full_path)
            )
            
        except Exception as e:
            logger.error(f"Error downloading document: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/api/document/<doc_id>/preview', methods=['GET'])
    def preview_document(doc_id: str):
        """Inline PDF preview in browser (Option A — no persisted duplicate on disk)."""
        if not pdf_inline_in_browser:
            return jsonify({'error': 'PDF inline preview disabled'}), 404
        try:
            file_path = request.args.get('path')
            if not file_path:
                return jsonify({'error': 'Document path required'}), 400
            full_path = _resolve_autodoc_path(file_path)
            if not full_path:
                return jsonify({'error': 'Document path not allowed'}), 400
            if not os.path.exists(full_path):
                return jsonify({'error': 'Document file not found'}), 404
            if not str(full_path).lower().endswith('.pdf'):
                return jsonify({'error': 'Preview only supported for PDF'}), 415
            return send_file(
                full_path,
                mimetype='application/pdf',
                as_attachment=False,
                download_name=os.path.basename(full_path),
            )
        except Exception as e:
            logger.error(f"Error previewing document: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/api/document/<doc_id>/client-path', methods=['GET'])
    def document_client_path(doc_id: str):
        """
        Return UNC and/or POSIX path for opening on the user's machine (session required).
        The browser uses this to launch the OS default app — no companion install.
        """
        if not browser_client_open_enabled:
            return jsonify({'success': False, 'error': 'Browser client-path open disabled'}), 503
        if not _open_mapping_configured():
            return jsonify({
                'success': False,
                'error': 'Open mapping not configured (OPEN_UNC_ROOT / OPEN_CLIENT_LOCAL_ROOT)',
            }), 503
        file_path = str(request.args.get('path') or '').strip()
        if not file_path:
            return jsonify({'success': False, 'error': 'path query parameter required'}), 400
        full_path = _resolve_autodoc_path(file_path)
        if not full_path or not os.path.exists(full_path):
            return jsonify({'success': False, 'error': 'Document path not allowed or missing'}), 404
        targets = _client_open_targets(full_path)
        if not targets:
            return jsonify({'success': False, 'error': 'No client path mapping for this file'}), 503
        body: Dict[str, Any] = {'success': True, 'doc_id': doc_id}
        body.update(targets)
        return jsonify(body)

    @app.route('/api/open-tokens/mint', methods=['POST'])
    def open_token_mint():
        """Mint a short-lived signed token for companion redeem (browser must send CSRF)."""
        if not companion_enabled:
            return jsonify({'success': False, 'error': 'Companion open disabled'}), 503
        if not _open_mapping_configured():
            return jsonify({
                'success': False,
                'error': (
                    'Open mapping not configured (open.unc_root / open.unc_roots '
                    'and/or open.client_local_root + open.local_root)'
                ),
            }), 503
        csrf_header = str(request.headers.get('X-CSRF-Token', '') or '')
        if not _csrf_token_is_valid(csrf_header):
            return jsonify({'success': False, 'error': 'CSRF token invalid or missing'}), 400
        try:
            data = request.get_json() or {}
            doc_id = str(data.get('doc_id') or '').strip()
            file_path = data.get('path')
            if not doc_id or not file_path:
                return jsonify({'success': False, 'error': 'doc_id and path required'}), 400
            full_path = _resolve_autodoc_path(str(file_path).strip())
            if not full_path or not os.path.exists(full_path):
                return jsonify({'success': False, 'error': 'Document path not allowed or missing'}), 400
            if not _can_open_via_companion(full_path):
                return jsonify({'success': False, 'error': 'No open mapping for this file'}), 503
            rel = str(file_path).strip()
            token = open_token_manager.mint(rel, doc_id)
            api_base = public_base_url_config or request.url_root.rstrip('/')
            redeem_url = f"{api_base}/api/open-tokens/redeem"
            companion_href = (
                f"{companion_uri_scheme}:open?token={quote(token, safe='')}"
                f"&apiBase={quote(api_base, safe='')}"
            )
            return jsonify({
                'success': True,
                'token': token,
                'redeem_url': redeem_url,
                'companion_href': companion_href,
                'doc_id': doc_id,
            })
        except Exception as e:
            logger.error(f"open_token_mint: {e}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/open-tokens/redeem', methods=['POST'])
    def open_token_redeem():
        """
        Redeem token → UNC and/or client-local path. No login (companion calls with Bearer).
        """
        if not companion_enabled:
            return jsonify({'success': False, 'error': 'Companion open disabled'}), 503
        try:
            auth = str(request.headers.get('Authorization', '') or '')
            token = ''
            if auth.lower().startswith('bearer '):
                token = auth[7:].strip()
            if not token:
                body = request.get_json(silent=True) or {}
                token = str(body.get('token') or '').strip()
            if not token:
                return jsonify({'success': False, 'error': 'Bearer token required'}), 400
            payload = open_token_manager.verify_and_consume(token, consume=True)
            if not payload:
                return jsonify({'success': False, 'error': 'Invalid or expired token'}), 401
            full_path = _resolve_autodoc_path(payload['rel'])
            if not full_path or not os.path.exists(full_path):
                return jsonify({'success': False, 'error': 'File no longer available'}), 410
            unc = _unc_for_resolved_path(full_path)
            client_path = _client_path_for_resolved_path(full_path)
            if not unc and not client_path:
                return jsonify({'success': False, 'error': 'No open mapping'}), 503
            body: Dict[str, Any] = {'success': True}
            if unc:
                body['unc'] = unc
            if client_path:
                body['path'] = client_path
            return jsonify(body)
        except Exception as e:
            logger.error(f"open_token_redeem: {e}", exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/open-tokens/spec', methods=['GET'])
    def open_tokens_spec():
        """Minimal OpenAPI 3 description for mint/redeem (unauthenticated read)."""
        spec = {
            'openapi': '3.0.3',
            'info': {
                'title': 'DocBridge open tokens',
                'version': '1.0.0',
            },
            'paths': {
                '/api/open-tokens/mint': {
                    'post': {
                        'summary': 'Mint signed open token (session + X-CSRF-Token)',
                        'requestBody': {
                            'required': True,
                            'content': {
                                'application/json': {
                                    'schema': {
                                        'type': 'object',
                                        'required': ['doc_id', 'path'],
                                        'properties': {
                                            'doc_id': {'type': 'string'},
                                            'path': {
                                                'type': 'string',
                                                'description': 'Relative path under AutoDoc root',
                                            },
                                        },
                                    }
                                }
                            },
                        },
                        'responses': {'200': {'description': 'token payload'}},
                    }
                },
                '/api/open-tokens/redeem': {
                    'post': {
                        'summary': 'Redeem token for UNC (Authorization: Bearer)',
                        'responses': {'200': {'description': '{success, unc}'}},
                    }
                },
            },
        }
        return jsonify(spec)

    @app.route('/api/health', methods=['GET'])
    def health():
        """Health check endpoint."""
        return jsonify({
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'semantix_api': api_client.health_check()
        })

    @app.route('/api/analytics/relevance-feedback', methods=['POST'])
    def relevance_feedback():
        """
        Proxy: POST /secured/analytics/relevance-feedback (per-query relevance 1–5).
        """
        try:
            data = request.get_json() or {}
            pointer = (data.get('pointer') or '').strip()
            if not pointer:
                return jsonify({'success': False, 'error': 'pointer ist erforderlich'}), 400
            score = data.get('relevance_score')
            if score is None:
                return jsonify({'success': False, 'error': 'relevance_score ist erforderlich'}), 400
            try:
                score_i = int(score)
            except (TypeError, ValueError):
                return jsonify({'success': False, 'error': 'relevance_score muss eine Zahl sein'}), 400
            if score_i < 1 or score_i > 5:
                return jsonify({'success': False, 'error': 'relevance_score muss zwischen 1 und 5 liegen'}), 400
            qsid = data.get('query_session_id')
            qsid_s = (str(qsid).strip() if qsid is not None else '') or None

            raw = api_client.post_relevance_feedback(
                pointer=pointer,
                relevance_score=score_i,
                query_session_id=qsid_s,
            )
            return jsonify({'success': True, 'semantix': raw}), 202
        except ValueError as e:
            return jsonify({'success': False, 'error': str(e)}), 400
        except Exception as e:
            logger.warning('Relevance feedback failed: %s', e, exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 502

    @app.route('/api/document/rating', methods=['GET', 'POST'])
    def document_rating():
        """
        Proxy: GET/POST /secured/document/rating (permanent importance/quality).
        """
        try:
            if request.method == 'GET':
                pointer = (request.args.get('pointer') or '').strip()
                if not pointer:
                    return jsonify({'success': False, 'error': 'pointer ist erforderlich'}), 400
                raw = api_client.get_document_rating(pointer)
                return jsonify({
                    'success': True,
                    'rating': raw.get('rating'),
                    'relevance_feedback': raw.get('relevance_feedback'),
                    'message': raw.get('message'),
                })

            data = request.get_json() or {}
            pointer = (data.get('pointer') or '').strip()
            if not pointer:
                return jsonify({'success': False, 'error': 'pointer ist erforderlich'}), 400
            imp = data.get('importance_score')
            qual = data.get('quality_score')
            imp_i = None
            qual_i = None
            if imp is not None and imp != '':
                try:
                    imp_i = int(imp)
                except (TypeError, ValueError):
                    return jsonify({'success': False, 'error': 'importance_score ungültig'}), 400
            if qual is not None and qual != '':
                try:
                    qual_i = int(qual)
                except (TypeError, ValueError):
                    return jsonify({'success': False, 'error': 'quality_score ungültig'}), 400
            if imp_i is not None and (imp_i < 1 or imp_i > 5):
                return jsonify({'success': False, 'error': 'importance_score muss 1–5 sein'}), 400
            if qual_i is not None and (qual_i < 1 or qual_i > 5):
                return jsonify({'success': False, 'error': 'quality_score muss 1–5 sein'}), 400
            if imp_i is None and qual_i is None:
                return jsonify({
                    'success': False,
                    'error': 'Mindestens importance_score oder quality_score angeben',
                }), 400

            raw = api_client.post_document_rating(
                pointer=pointer,
                importance_score=imp_i,
                quality_score=qual_i,
            )
            return jsonify({
                'success': True,
                'pointer': raw.get('pointer'),
                'importance_score': raw.get('importance_score'),
                'quality_score': raw.get('quality_score'),
                'last_updated': raw.get('last_updated'),
                'message': raw.get('message'),
            })
        except ValueError as e:
            return jsonify({'success': False, 'error': str(e)}), 400
        except Exception as e:
            logger.warning('Document rating API failed: %s', e, exc_info=True)
            return jsonify({'success': False, 'error': str(e)}), 502
    
    @app.route('/api/stats', methods=['GET'])
    def stats():
        """Get usage statistics."""
        return jsonify({
            'status': 'operational',
            'timestamp': datetime.now().isoformat()
        })
    
    return app


def _effective_cosine_distance(result: Dict[str, Any]) -> Optional[float]:
    """
    Knovas-style distance: 0 = best, higher = worse.
    Prefer API cosine_distance; else derive from cosine_similarity (1 - sim).
    """
    cd = result.get("cosine_distance")
    if cd is not None and str(cd).strip() != "":
        try:
            return float(cd)
        except (TypeError, ValueError):
            pass
    cs = result.get("cosine_similarity")
    if cs is not None and str(cs).strip() != "":
        try:
            return max(0.0, min(1.0, 1.0 - float(cs)))
        except (TypeError, ValueError):
            pass
    return None


def _search_result_haystack(result: Dict[str, Any]) -> str:
    """Lowercased text used for strict / exact-style matching."""
    parts = [
        result.get('title'),
        result.get('snippet'),
        result.get('content'),
        result.get('ingested_summary'),
        result.get('description'),
        result.get('path'),
        result.get('doc_id'),
        result.get('page_number'),
        result.get('sentence_number'),
    ]
    return ' '.join(str(p) for p in parts if p is not None and str(p).strip()).lower()


def _apply_search_refinement(
    enhanced: Dict[str, Any],
    query: str,
    filters: Dict[str, Any],
    config,
) -> Dict[str, Any]:
    """
    Tighten results after Knovas returns (semantic search is loose by default).

    - exact_match (UI checkbox): every significant token must appear as a substring
      in title/snippet/content/description/path (after enrichment).
    - min_similarity_score: minimum internal *similarity* (higher = better; same scale as
      cosine_similarity, or 1 - cosine_distance). NOT the same as max distance.
    - max_cosine_distance: drop hits whose cosine *distance* exceeds this (0 = best).
      If the API sends no scores, see enforce_similarity_threshold_when_set.
    """
    out: List[Dict[str, Any]] = list(enhanced.get('results', []))

    min_score = config.get_float('web.search.min_similarity_score', 0.0)
    if min_score > 0.0 and out:
        scores = [float(r.get('score') or 0) for r in out]
        max_s = max(scores) if scores else 0.0
        if max_s > 1e-9:
            out = [r for r in out if float(r.get('score') or 0) >= min_score]
        else:
            enforce = config.get_bool(
                'web.search.enforce_similarity_threshold_when_set', True
            )
            if enforce:
                logger.warning(
                    "min_similarity_score=%s is set but search results have no similarity "
                    "values (Knovas may use a different JSON field). Returning no results. "
                    "Set web.search.enforce_similarity_threshold_when_set: false to show "
                    "unfiltered API results until the API exposes scores.",
                    min_score,
                )
                out = []
            else:
                logger.warning(
                    "min_similarity_score=%s ignored: all result scores are zero/missing.",
                    min_score,
                )

    max_dist = config.get_float("web.search.max_cosine_distance", -1.0)
    if max_dist >= 0.0 and out:
        kept: List[Dict[str, Any]] = []
        for r in out:
            d = _effective_cosine_distance(r)
            if d is None:
                kept.append(r)
            elif d <= max_dist + 1e-9:
                kept.append(r)
        out = kept

    if filters.get('exact_match'):
        min_term = config.get_int('web.search.strict_match_min_term_length', 2)
        qlow = (query or '').strip().lower()
        terms = [t for t in re.split(r'\s+', qlow) if len(t) >= min_term]

        def matches(r: Dict[str, Any]) -> bool:
            hay = _search_result_haystack(r)
            if terms:
                return all(t in hay for t in terms)
            return qlow in hay if qlow else True

        out = [r for r in out if matches(r)]

    # Highest similarity first; tie-break by doc_id for stable ordering.
    out.sort(
        key=lambda r: (-float(r.get('score') or 0), str(r.get('doc_id') or '')),
    )

    refined = enhanced.copy()
    refined['results'] = out
    refined['total'] = len(out)
    return refined


def _enrichment_title_matches_query(
    title: str,
    query: str,
    exact_match: bool,
    min_term: int,
) -> bool:
    """Whether filename/title should count as a hit for this query (case-insensitive)."""
    t = (title or "").lower()
    qlow = (query or "").strip().lower()
    if not t or not qlow:
        return False
    if exact_match:
        terms = [x for x in re.split(r"\s+", qlow) if len(x) >= min_term]
        if terms:
            return all(term in t for term in terms)
        return qlow in t
    if qlow in t:
        return True
    for tok in re.split(r"\W+", qlow):
        if len(tok) >= 3 and tok in t:
            return True
    return False


def _supplement_results_from_enrichment_filenames(
    query: str,
    results: List[Dict[str, Any]],
    filters: Dict[str, Any],
    config,
) -> List[Dict[str, Any]]:
    """
    Add OneDrive (JSONL) rows whose *title* matches the query even when Knovas
    vector search does not return them (e.g. match in filename only).
    """
    if not config.get_bool("web.search.supplement_filename_matches", False):
        return results
    min_sq = config.get_int("web.search.supplement_min_query_length", 3)
    qstrip = (query or "").strip()
    if len(qstrip) < min_sq:
        return results

    enrichment = _load_search_enrichment()
    if not enrichment:
        return results

    existing = {str(r.get("doc_id")) for r in results if r.get("doc_id") is not None}
    min_term = config.get_int("web.search.strict_match_min_term_length", 2)
    exact = bool(filters.get("exact_match"))

    extra: List[Dict[str, Any]] = []
    for did, meta in enrichment.items():
        if did in existing:
            continue
        title = meta.get("title") or ""
        if not _enrichment_title_matches_query(title, query, exact, min_term):
            continue
        row: Dict[str, Any] = {
            "doc_id": did,
            "path": did,
            "score": 0.01,
            "title": title,
            "source": "semantix",
            "match_supplement": "filename",
        }
        if meta.get("doc_type"):
            row["type"] = meta["doc_type"]
            row["doc_type"] = meta["doc_type"]
        if meta.get("description"):
            row["description"] = meta["description"]
        if meta.get("akten_id"):
            row["akten_id"] = meta["akten_id"]
        wu = meta.get("web_url")
        if wu and _is_safe_http_url(wu):
            row["external_url"] = wu.strip()
            row["file_exists"] = True
            row["can_open"] = True
            row["open_mode"] = "external"
        else:
            row.setdefault("file_exists", False)
            row.setdefault("can_open", False)
        extra.append(row)

    if not extra:
        return results

    merged = list(results) + extra
    merged.sort(
        key=lambda r: (0 if r.get("match_supplement") == "filename" else 1, -float(r.get("score") or 0), str(r.get("doc_id") or "")),
    )
    return merged


def _is_safe_http_url(url: Optional[str]) -> bool:
    if not url or not isinstance(url, str):
        return False
    u = url.strip().lower()
    return u.startswith("https://") or u.startswith("http://")


def _load_search_enrichment() -> Dict[str, dict]:
    """
    Load JSONL written by docbridge-sync (OneDrive webUrl, title, etc.).
    Last line per doc_id wins. Cached by file mtime.
    """
    global _search_enrichment_cache, _search_enrichment_mtime
    path = os.getenv("SEARCH_ENRICHMENT_PATH", "").strip()
    if not path:
        return {}
    try:
        if not os.path.isfile(path):
            return {}
        mtime = os.path.getmtime(path)
        if mtime == _search_enrichment_mtime and _search_enrichment_cache:
            return _search_enrichment_cache
    except OSError:
        return {}

    by_id: Dict[str, dict] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                did = rec.get("doc_id")
                if did is not None:
                    by_id[str(did)] = rec
        _search_enrichment_cache = by_id
        _search_enrichment_mtime = mtime
    except Exception as e:
        logger.warning("Could not load search enrichment from %s: %s", path, e)

    return _search_enrichment_cache


def _enhance_search_results(
    results: Dict[str, Any],
    file_handler: AutoDocFileHandler
) -> Dict[str, Any]:
    """
    Enhance search results with additional metadata.
    
    Args:
        results: Raw search results from API
        file_handler: AutoDocFileHandler instance
        
    Returns:
        Enhanced results
    """
    enrichment = _load_search_enrichment()
    enhanced_results = results.copy()
    
    if 'results' not in enhanced_results:
        return enhanced_results

    for result in enhanced_results['results']:
        doc_id = str(result.get("doc_id") or result.get("pointer") or "")
        meta = enrichment.get(doc_id) if doc_id else None
        if meta:
            if meta.get("title"):
                result["title"] = meta["title"]
            if meta.get("doc_type"):
                result["type"] = meta["doc_type"]
                result["doc_type"] = meta["doc_type"]
            if meta.get("description"):
                result["description"] = meta["description"]
            if meta.get("akten_id"):
                result["akten_id"] = meta["akten_id"]
            meta_date = (
                meta.get("date")
                or meta.get("document_date")
                or meta.get("timestamp")
                or meta.get("modified_at")
            )
            if meta_date and not result.get("document_date") and not result.get("date"):
                result["document_date"] = meta_date
                result["date"] = meta_date
            wu = meta.get("web_url")
            if wu and _is_safe_http_url(wu):
                result["external_url"] = wu.strip()

        fp = (result.get("path") or "").strip()
        if _is_safe_http_url(fp):
            result["external_url"] = (result.get("external_url") or fp).strip()

        if result.get("external_url") and _is_safe_http_url(result["external_url"]):
            result["file_exists"] = True
            result["can_open"] = True
            result["open_mode"] = "external"
            continue

        file_path = result.get("path")
        if file_path:
            rel = _rel_path_for_autodoc(str(file_path))
            full_path = os.path.join(file_handler.autodoc_path, rel)
            result["file_exists"] = os.path.exists(full_path)
            result["autodoc_rel_path"] = rel
            result["can_open"] = result["file_exists"]
            if result["file_exists"]:
                try:
                    stat = os.stat(full_path)
                    result["file_size"] = stat.st_size
                    result["modified_at"] = datetime.fromtimestamp(
                        stat.st_mtime
                    ).isoformat()
                except Exception:
                    pass
        else:
            result.setdefault("file_exists", False)
            result.setdefault("can_open", False)

    return enhanced_results


def _open_file_external(file_path: str, config) -> bool:
    """
    Open file with external application.
    
    Args:
        file_path: Path to file
        config: Configuration object
        
    Returns:
        True if successful, False otherwise
    """
    try:
        system = platform.system()
        
        external_app = config.get('web.document_handler.external_app')
        
        if external_app and os.path.exists(external_app):
            subprocess.Popen([external_app, file_path])
            return True
        
        if system == 'Windows':
            os.startfile(file_path)
        elif system == 'Darwin':
            subprocess.Popen(['open', file_path])
        else:
            subprocess.Popen(['xdg-open', file_path])
        
        return True
        
    except Exception as e:
        logger.error(f"Error opening file externally: {e}")
        return False


def main():
    """Main entry point for web interface."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='DocBridge Document Search Web Interface'
    )
    parser.add_argument(
        '--config',
        type=str,
        help='Path to configuration file'
    )
    parser.add_argument(
        '--host',
        type=str,
        help='Host to bind to'
    )
    parser.add_argument(
        '--port',
        type=int,
        help='Port to bind to'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug mode'
    )
    parser.add_argument(
        '--log-level',
        type=str,
        default='INFO',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help='Logging level'
    )
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('docbridge_web.log')
        ]
    )
    
    app = create_app(config_path=args.config)
    
    config = get_config()
    
    host = args.host or config.get('web.host', '0.0.0.0')
    port = args.port or config.get_int('web.port', 8080)
    debug = args.debug or config.get_bool('web.debug', False)
    
    logger.info(f"Starting DocBridge Web Interface on {host}:{port}")
    logger.info(f"Debug mode: {debug}")
    
    app.run(
        host=host,
        port=port,
        debug=debug,
        threaded=True
    )


if __name__ == '__main__':
    main()
