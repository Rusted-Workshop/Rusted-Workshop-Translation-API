"""
RabbitMQ 消息队列服务
"""

import json
import os
from typing import Callable, Optional

import pika
from pika.adapters.blocking_connection import BlockingChannel


class RabbitMQService:
    """RabbitMQ 消息队列服务"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5672,
        username: str = "guest",
        password: str = "guest",
        virtual_host: str = "/",
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.virtual_host = virtual_host
        self.connection: Optional[pika.BlockingConnection] = None
        self.channel: Optional[BlockingChannel] = None

    def connect(self):
        """建立连接"""
        credentials = pika.PlainCredentials(self.username, self.password)
        parameters = pika.ConnectionParameters(
            host=self.host,
            port=self.port,
            virtual_host=self.virtual_host,
            credentials=credentials,
            heartbeat=600,
            blocked_connection_timeout=300,
        )
        self.connection = pika.BlockingConnection(parameters)
        self.channel = self.connection.channel()

    def close(self):
        """关闭连接"""
        if self.connection and not self.connection.is_closed:
            self.connection.close()

    def declare_queue(self, queue_name: str, durable: bool = True):
        """声明队列"""
        if not self.channel:
            self.connect()
        if self.channel:
            self.channel.queue_declare(queue=queue_name, durable=durable)

    def publish_message(
        self, queue_name: str, message: dict, priority: int = 0
    ) -> None:
        """
        发布消息到队列

        Args:
            queue_name: 队列名称
            message: 消息内容（字典）
            priority: 消息优先级
        """
        if not self.channel:
            self.connect()

        if self.channel:
            self.channel.basic_publish(
                exchange="",
                routing_key=queue_name,
                body=json.dumps(message),
                properties=pika.BasicProperties(
                    delivery_mode=2,  # 消息持久化
                    priority=priority,
                ),
            )

    def consume_messages(
        self, queue_name: str, callback: Callable, prefetch_count: int = 1
    ):
        """
        消费队列消息

        Args:
            queue_name: 队列名称
            callback: 消息处理回调函数
            prefetch_count: 预取消息数量
        """
        if not self.channel:
            self.connect()

        if self.channel:
            self.channel.basic_qos(prefetch_count=prefetch_count)
            self.channel.basic_consume(
                queue=queue_name, on_message_callback=callback, auto_ack=False
            )

            print(f"开始监听队列: {queue_name}")
            self.channel.start_consuming()

    def ack_message(self, delivery_tag):
        """确认消息"""
        if self.channel:
            self.channel.basic_ack(delivery_tag=delivery_tag)

    def nack_message(self, delivery_tag, requeue: bool = True):
        """拒绝消息"""
        if self.channel:
            self.channel.basic_nack(delivery_tag=delivery_tag, requeue=requeue)


def get_rabbitmq_service() -> RabbitMQService:
    """获取RabbitMQ服务实例（从环境变量读取配置）"""
    return RabbitMQService(
        host=os.getenv("RABBITMQ_HOST", "localhost"),
        port=int(os.getenv("RABBITMQ_PORT", "5672")),
        username=os.getenv("RABBITMQ_USERNAME", "guest"),
        password=os.getenv("RABBITMQ_PASSWORD", "guest"),
        virtual_host=os.getenv("RABBITMQ_VHOST", "/"),
    )
