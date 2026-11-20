#!/usr/bin/env python3
"""
OMNI 1.1 FLASH VIDEO STUDIO — Cross-Platform Server CLI (`omni_cli.py`)
Works identically on Linux servers, macOS, and Windows without requiring PowerShell.

Commands:
  python3 omni_cli.py status
  python3 omni_cli.py set-cookie --cookie "SID=...; __Secure-1PSIDTS=..." --doc-id "1x5..." --authuser 0 --api-key "AIza..."
  python3 omni_cli.py rotate-cookie
  python3 omni_cli.py scene --prompt "Continuous cinematic shot..." --image assets_1080p/asset_01_kenh_ket_noi_1080p.png --output outputs/scene1.mp4
  python3 omni_cli.py server --port 8080
"""

import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO_ROOT)

from omni_server import (
    DEFAULT_SESSION_PATH,
    bootstrap_session_from_env,
    get_session_status,
    rotate_google_cookies_headless,
    run_vids_rpc_edit_or_generate,
    update_session_payload,
)


def main():
    bootstrap_session_from_env()
    parser = argparse.ArgumentParser(description="Omni 1.1 Flash Video Studio — Unified Server CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # 1. status
    sub.add_parser("status", help="Check vids_session.json cookie health and GEMINI_API_KEY status")

    # 2. set-cookie
    p_set = sub.add_parser("set-cookie", help="Configure Google Vids Cookie / cURL and GEMINI_API_KEY on the server")
    p_set.add_argument("--cookie", help="Full Cookie header string from docs.google.com / vids.new")
    p_set.add_argument("--curl-file", help="Path to file containing raw browser cURL command")
    p_set.add_argument("--doc-id", help="Google Vids document ID")
    p_set.add_argument("--authuser", default="0", help="Google account index (0, 1, 2...)")
    p_set.add_argument("--api-key", help="GEMINI_API_KEY for Gemini 3.8 Flash TTS & Lyria RealTime")

    # 3. rotate-cookie
    sub.add_parser("rotate-cookie", help="Rotate __Secure-1PSIDTS headless over HTTPS (no Chrome required)")

    # 4. scene
    p_sc = sub.add_parser("scene", help="Generate a 3s-15s Omni 1.1 Flash scene (supports 15s native clips + StructuredImage ingredient)")
    p_sc.add_argument("--prompt", "-p", required=True, help="Scene visual prompt")
    p_sc.add_argument("--image", "-i", action="append", dest="images", help="Ingredient image path(s) for StructuredImage (Node[11])")
    p_sc.add_argument("--prior-node", action="append", dest="priors", help="Prior scene _node.json path(s) for chaining")
    p_sc.add_argument("--duration", type=int, default=15, help="Duration in seconds (3-15, supports 10s or 15s; default: 15)")
    p_sc.add_argument("--no-upscale", action="store_true", help="Keep raw 720p instead of upscaling to 1080p")
    p_sc.add_argument("--output", "-o", required=True, help="Output MP4 file path")

    # 5. server
    p_srv = sub.add_parser("server", help="Start the HTTP REST API & Web Studio Server")
    p_srv.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    p_srv.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")

    args = parser.parse_args()

    if args.cmd == "status":
        print(json.dumps(get_session_status(), indent=2, ensure_ascii=False))
    elif args.cmd == "set-cookie":
        payload = {"authuser": args.authuser}
        if args.doc_id:
            payload["doc_id"] = args.doc_id
        if args.api_key:
            payload["gemini_api_key"] = args.api_key
        if args.curl_file:
            with open(args.curl_file, "r", encoding="utf-8") as f:
                payload["curl"] = f.read()
        elif args.cookie:
            payload["cookie"] = args.cookie
        res = update_session_payload(payload)
        print(json.dumps({"update": res, "status": get_session_status()}, indent=2, ensure_ascii=False))
    elif args.cmd == "rotate-cookie":
        res = rotate_google_cookies_headless(DEFAULT_SESSION_PATH)
        print(json.dumps(res, indent=2, ensure_ascii=False))
    elif args.cmd == "scene":
        out_abs = os.path.abspath(args.output)
        node_json = os.path.splitext(out_abs)[0] + "_node.json"
        res = run_vids_rpc_edit_or_generate(
            prompt_text=args.prompt,
            output_path=out_abs,
            prior_node_files=args.priors or [],
            image_files=args.images or [],
            duration_sec=args.duration,
            save_response_json=node_json,
            upscale_1080p=not args.no_upscale,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
    elif args.cmd == "server":
        sys.argv = [sys.argv[0], "--host", args.host, "--port", str(args.port)]
        from omni_server import main as srv_main

        srv_main()


if __name__ == "__main__":
    main()
