import argparse
import sys
from aiohttp import web
import multiprocessing
import socket
import json
from datetime import datetime
import pymongo
import os
import logging

logger = logging.getLogger(__name__)
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(ROOT_DIR, "front-init")

MONGO_HOST = os.environ.get("MONGO_HOST", "mongo")
MONGO_PORT = int(os.environ.get("MONGO_PORT", 27017))

# ---- Socket Server (порт 5000) ----
def socket_server():
    try:
        client = pymongo.MongoClient(MONGO_HOST, MONGO_PORT)
        db = client["messages_db"]
        collection = db["messages"]

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("0.0.0.0", 5000))
        s.listen(5)
        logger.info("Socket server started on port 5000")

        while True:
            try:
                conn, addr = s.accept()
                logger.info(f"Connection accepted from {addr}")
                data = conn.recv(4096)
                if not data:
                    conn.close()
                    continue

                msg_dict = json.loads(data.decode('utf-8'))
                msg_dict["date"] = datetime.now().isoformat(" ")
                collection.insert_one(msg_dict)
                logger.info(f"Message saved to MongoDB: {msg_dict}")
            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error: {e}")
            except pymongo.errors.PyMongoError as e:
                logger.error(f"MongoDB error: {e}")
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
            finally:
                conn.close()
    except Exception as e:
        logger.critical(f"Critical error in socket server: {e}")
    finally:
        s.close()

# ---- HTTP-сервер (порт 3000) ----

async def handle_index(request):
    return web.FileResponse(f"{STATIC_DIR}/index.html")

async def handle_message_get(request):
    return web.FileResponse(f"{STATIC_DIR}/message.html")

async def handle_message_post(request):
    try:
        data = await request.post()
        username = data.get("username")
        message = data.get("message")

        msg_dict = {
            "username": username,
            "message": message
        }

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("localhost", 5000))
        sock.sendall(json.dumps(msg_dict).encode('utf-8'))
        sock.close()
        logger.info(f"Message sent to socket server: {msg_dict}")

        return web.FileResponse(f"{STATIC_DIR}/sent.html")
    except Exception as e:
        logger.error(f"Error sending data to socket server: {e}")
        return web.Response(text="Error sending message. Please try again later.", content_type='text/html')

async def handle_static(request):
    filename = request.match_info.get('filename', '')
    filepath = f"{STATIC_DIR}/{filename}"
    if os.path.exists(filepath):
        return web.FileResponse(filepath)
    else:
        return await handle_404(request)

async def handle_404(request):
    return web.FileResponse("/error.html", status=404)

async def init_app():
    app = web.Application()
    app.router.add_get('/', handle_index)
    app.router.add_get('/message', handle_message_get)
    app.router.add_post('/message', handle_message_post)
    app.router.add_get('/{filename}', handle_static)
    app.router.add_get('/{tail:.*}', handle_404)
    app.router.add_post('/{tail:.*}', handle_404)
    return app

def run_web_server():
    web.run_app(init_app(), host='0.0.0.0', port=3000)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simple web application with socket server.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Set the logging level (default: INFO).")
    parser.add_argument("--path-to-log", default="http_server.log", help="Path to the log file (default: http_server.log).")
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(args.path_to_log, mode="a", encoding="utf-8"),
        ],
    )

    p = multiprocessing.Process(target=socket_server, daemon=True)
    p.start()

    run_web_server()
