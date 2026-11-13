import json
import os
import sqlite3
import time
from datetime import datetime

import pika

DB_PATH = os.getenv("DB_PATH", "/data/parksim.db")
RABBIT_HOST = os.getenv("RABBIT_HOST", "rabbitmq")
RABBIT_USER = os.getenv("RABBIT_USER", "parksim_app")
RABBIT_PASSWORD = os.getenv("RABBIT_PASSWORD", "ParkSim2026!")
RABBIT_VHOST = os.getenv("RABBIT_VHOST", "parksim")
QUEUE = "parksim.events"


def connect():
    credentials = pika.PlainCredentials(RABBIT_USER, RABBIT_PASSWORD)
    params = pika.ConnectionParameters(host=RABBIT_HOST, port=5672, virtual_host=RABBIT_VHOST,
        credentials=credentials, heartbeat=30, blocked_connection_timeout=10)
    return pika.BlockingConnection(params)


def save_event(message):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    try:
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
        conn.execute("INSERT INTO events(event,payload,created_at) VALUES(?,?,?)",
            (message.get("event", "unknown"), json.dumps(message.get("payload", {}), ensure_ascii=False),
             message.get("at") or datetime.now().astimezone().isoformat(timespec="seconds")))
        conn.commit()
    finally:
        conn.close()


def main():
    while True:
        connection = None
        try:
            connection = connect()
            channel = connection.channel()
            channel.queue_declare(queue=QUEUE, durable=True)
            channel.basic_qos(prefetch_count=10)
            def callback(ch, method, properties, body):
                try:
                    save_event(json.loads(body.decode("utf-8")))
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                except Exception as exc:
                    print(f"Falha ao persistir evento: {exc}", flush=True)
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            channel.basic_consume(queue=QUEUE, on_message_callback=callback)
            print("ParkSIM worker conectado ao RabbitMQ; aguardando eventos.", flush=True)
            channel.start_consuming()
        except Exception as exc:
            print(f"Worker aguardando RabbitMQ: {exc}. Nova tentativa em 5s.", flush=True)
            time.sleep(5)
        finally:
            try:
                if connection and connection.is_open:
                    connection.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
