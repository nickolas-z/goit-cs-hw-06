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
from http import HTTPStatus
import traceback

logger = logging.getLogger(__name__)
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(ROOT_DIR, "front-init")


def init_mongo_client(mongo_host, mongo_port):
    """
    Initializes a MongoDB client.
    params:
        mongo_host (str): The MongoDB host.
        mongo_port (int): The MongoDB port.
    returns:
        pymongo.MongoClient: The MongoDB client.
    """
    
    logger.debug("Initializing MongoDB client.")
    try:
        return pymongo.MongoClient(mongo_host, mongo_port)
    except Exception as e:
        logger.critical(f"Failed to initialize MongoDB client: {e}")
        sys.exit(1)


def create_socket_server(socket_host, socket_port):
    """
    Creates a socket server.
    params:
        socket_host (str): The hostname of the socket server.
        socket_port (int): The port of the socket server.
    returns:
        socket.socket: The socket server.
    """

    logger.debug("Creating socket server.")
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind((socket_host, socket_port))
        s.listen(5)
        logger.info(f"Socket server started on port {socket_port}")
        return s
    except Exception as e:
        logger.critical(f"Failed to create socket server: {e}")
        sys.exit(1)


def handle_client_connection(conn, collection):
    """
    Handles a client connection by receiving data, parsing it, and saving it to MongoDB.
    params:
        conn (socket.socket): The client connection.
        collection (pymongo.collection.Collection): The MongoDB collection.
    """
    try:
        logger.debug("Handling client connection.")
        data = conn.recv(4096)
        logger.debug(f"Received data: {data}")
        if not data:
            logger.debug("No data received. Closing connection.")
            return
        msg_dict = json.loads(data.decode("utf-8"))

        if msg_dict.get("action") == "get_messages":
            logger.debug("Action: get_messages")
            messages = list(collection.find({}, {"_id": 0}))
            response = json.dumps(messages, ensure_ascii=False).encode("utf-8")
            conn.sendall(response)
            logger.debug(f"Sent messages: {messages}")
        else:
            msg_dict["date"] = datetime.now().isoformat(" ")
            logger.debug(f"Parsed message: {msg_dict}")
            collection.insert_one(msg_dict)
            logger.info(f"Message saved to MongoDB: {msg_dict}")
    except Exception as e:
        logger.error(
            f"Error handling client connection: {e}\n{traceback.format_exc()}"
        )
    finally:
        conn.close()


def socket_server(mongo_host, mongo_port, socket_host, socket_port):
    """
    Starts a socket server that listens for incoming connections and handles them.
    params:
        mongo_host (str): The MongoDB host.
        mongo_port (int): The MongoDB port.
        socket_host (str): The hostname of the socket server.
        socket_port (int): The port of the socket server.
    """

    try:
        logger.debug("Starting socket server.")
        client = init_mongo_client(mongo_host, mongo_port)
        collection = client["messages_db"]["messages"]
        s = create_socket_server(socket_host, socket_port)
        while True:
            conn, addr = s.accept()
            logger.info(f"Connection accepted from {addr}")
            handle_client_connection(conn, collection)
    except Exception as e:
        logger.critical(
            f"Critical error in socket server: {e}\n{traceback.format_exc()}"
        )
    finally:
        s.close()


async def serve_file(filename, status=HTTPStatus.OK):
    """
    Serves a file from the static directory.
    params:
        filename (str): The name of the file to serve.
        status (int): The HTTP status code (default: 200).
    returns:
        aiohttp.web.Response: The HTTP response to be sent back to the client.
    """
    filepath = os.path.join(STATIC_DIR, filename)
    logger.debug(f"Serving file: {filepath}")
    if os.path.exists(filepath):
        return web.FileResponse(filepath, status=status)

    logger.warning(f"File not found: {filepath}")
    return await serve_file("error.html", status=HTTPStatus.NOT_FOUND)


async def handle_message_post(request, socket_host, socket_port):
    """
    Handles a POST message request, validates the data, and sends it to a socket server.

    Args:
        request (aiohttp.web.Request): The incoming HTTP request.
        socket_host (str): The hostname of the socket server.
        socket_port (int): The port of the socket server.

    Returns:
        aiohttp.web.Response: The HTTP response to be sent back to the client.

    Raises:
        ValueError: If the username or message is empty.
        Exception: For any other errors that occur during processing.
    """
    try:
        logger.debug("Handling POST message request.")
        data = await request.post()
        username = data.get("username", "").strip()
        message = data.get("message", "").strip()
        logger.debug(
            f"Received form data: username={username}, message={message}"
        )

        if not username or not message:
            raise ValueError("Username and message cannot be empty.")

        msg_dict = {"username": username, "message": message}
        with socket.create_connection((socket_host, socket_port)) as sock:
            sock.sendall(
                json.dumps(msg_dict, ensure_ascii=False).encode("utf-8")
            )
        logger.info(f"Message sent to socket server: {msg_dict}")
        return await serve_file("success.html")

    except ValueError as e:
        logger.error(f"Validation error: {e}")
        return await serve_file(
            "message-not-sent.html", status=HTTPStatus.BAD_REQUEST
        )

    except Exception as e:
        logger.error(
            f"Error sending data to socket server: {e}\n{traceback.format_exc()}"
        )
        return await serve_file(
            "message-not-sent.html", status=HTTPStatus.BAD_REQUEST
        )


async def handle_messages(socket_host, socket_port):
    """
    Handles a GET messages request, retrieves messages from a socket server, and returns them.
    params:
        socket_host (str): The hostname of the socket server.
        socket_port (int): The port of the socket server.
    returns:
        aiohttp.web.Response: The HTTP response to be sent back to the client.
    """

    try:
        logger.debug("Handling GET messages request.")
        with socket.create_connection((socket_host, socket_port)) as sock:
            sock.sendall(
                json.dumps({"action": "get_messages"}).encode("utf-8")
            )
            data = sock.recv(4096).decode("utf-8")

            if not data.strip():
                logger.warning("No data received from socket.")
                return await serve_file(
                    "error.html", status=HTTPStatus.BAD_REQUEST
                )

            try:
                messages = json.loads(data)
                logger.debug(f"Retrieved messages: {messages}")
                return web.json_response(messages)
            except json.JSONDecodeError:
                logger.error("Failed to decode JSON from socket.")
                return await serve_file(
                    "error.html", status=HTTPStatus.BAD_REQUEST
                )

    except Exception as e:
        logger.error(
            f"Error retrieving messages: {e}\n{traceback.format_exc()}"
        )
        return await serve_file("error.html", status=HTTPStatus.BAD_REQUEST)


async def init_app(mongo_host, mongo_port, socket_host, socket_port):
    """
    Initializes the web application with routes and static files.
    params:
        mongo_host (str): The MongoDB host.
        mongo_port (int): The MongoDB port.
        socket_host (str): The hostname of the socket server.
        socket_port (int): The port of the socket server.
    returns:
        aiohttp.web.Application: The initialized web application.
    """

    logger.debug("Initializing web application.")
    app = web.Application()
    app.router.add_get("/", lambda _: serve_file("index.html"))
    app.router.add_get("/message", lambda _: serve_file("message.html"))
    app.router.add_get("/all-messages", lambda _: serve_file("messages.html"))
    app.router.add_get(
        "/messages", lambda _: handle_messages(socket_host, socket_port)
    )
    app.router.add_post(
        "/message",
        lambda request: handle_message_post(request, socket_host, socket_port),
    )
    app.router.add_get(
        "/{filename}",
        lambda request: serve_file(request.match_info["filename"]),
    )
    app.router.add_static("/static/", STATIC_DIR)
    app.router.add_route(
        "*",
        "/{tail:.*}",
        lambda _: serve_file("error.html", status=HTTPStatus.NOT_FOUND),
    )
    return app


def run_web_server(
    mongo_host, mongo_port, web_host, web_port, socket_host, socket_port
):
    """
    Runs the web server.
    params:
        mongo_host (str): The MongoDB host.
        mongo_port (int): The MongoDB port.
        web_host (str): The web server host.
        web_port (int): The web server port.
        socket_host (str): The hostname of the socket server.
        socket_port (int): The port of the socket server.
    """
    logger.debug("Starting web server.")
    try:
        web.run_app(
            init_app(mongo_host, mongo_port, socket_host, socket_port),
            host=web_host,
            port=web_port,
        )
    except Exception as e:
        logger.critical(f"Failed to run web server: {e}\n{traceback.format_exc()}")
        sys.exit(1)


if __name__ == "__main__":
    """
    Main entry point of the application.
    """
    parser = argparse.ArgumentParser(
        description="Simple web application with socket server."
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level (default: INFO).",
    )
    parser.add_argument(
        "--path-to-log",
        default="logs/http_server.log",
        help="Path to the log file (default: http_server.log).",
    )
    parser.add_argument(
        "--mongo-host",
        default="mongodb",
        help="MongoDB host (default: mongodb).",
    )
    parser.add_argument(
        "--mongo-port",
        type=int,
        default=27017,
        help="MongoDB port (default: 27017).",
    )
    parser.add_argument(
        "--socket-host",
        default="0.0.0.0",
        help="Socket server host (default: 0.0.0.0).",
    )
    parser.add_argument(
        "--socket-port",
        type=int,
        default=5000,
        help="Socket server port (default: 5000).",
    )
    parser.add_argument(
        "--web-host",
        default="0.0.0.0",
        help="Web server host (default: 0.0.0.0).",
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=3000,
        help="Web server port (default: 3000).",
    )

    try:
        args = parser.parse_args()

        logging.basicConfig(
            level=args.log_level,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.StreamHandler(sys.stdout),
                logging.FileHandler(
                    args.path_to_log, mode="a", encoding="utf-8"
                ),
            ],
        )

        p = multiprocessing.Process(
            target=socket_server,
            args=(
                args.mongo_host,
                args.mongo_port,
                args.socket_host,
                args.socket_port,
            ),
            daemon=True,
        )
        p.start()
        run_web_server(
            args.mongo_host,
            args.mongo_port,
            args.web_host,
            args.web_port,
            args.socket_host,
            args.socket_port,
        )
    except Exception as e:
        logger.critical(
            f"Critical error in main: {e}\n{traceback.format_exc()}"
        )
        sys.exit(1)
