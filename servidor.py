#!/usr/bin/env python3
"""
Servidor HTTP simples para servir o dashboard de trading.
Uso: python3 servidor.py
Acesso: http://localhost:8000/dashboard.html
"""
import http.server
import os

PORT = 8000
DIR  = os.path.dirname(os.path.abspath(__file__))

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR, **kwargs)

    def end_headers(self):
        # Permite fetch() de qualquer origem (necessário para browser local)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        # Mostra apenas pedidos relevantes, silencia favicon e recursos estáticos
        path = args[0] if args else ""
        if "favicon" not in str(path):
            super().log_message(fmt, *args)

if __name__ == "__main__":
    os.chdir(DIR)
    print(f"[servidor] A servir {DIR}")
    print(f"[servidor] Dashboard: http://localhost:{PORT}/dashboard.html")
    print(f"[servidor] CTRL+C para parar\n")
    with http.server.HTTPServer(("", PORT), Handler) as httpd:
        httpd.serve_forever()
