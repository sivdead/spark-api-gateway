import _thread as thread
import base64
import datetime
import hashlib
import hmac
import json
from urllib.parse import urlparse
import ssl
from datetime import datetime
from time import mktime, time
from urllib.parse import urlencode
from wsgiref.handlers import format_date_time
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed
import websocket
import secrets
import string


class SparkChat(object):
    answer = ""
    function_call: dict[str, object] | None = None
    usage = None

    def __init__(self, app_id, api_key, api_secret, spark_chat_url, domain):
        self.app_id = app_id
        self.api_key = api_key
        self.api_secret = api_secret
        self.host = urlparse(spark_chat_url).netloc
        self.path = urlparse(spark_chat_url).path
        self.spark_chat_url = spark_chat_url
        self.domain = domain

    def generate_random_id(self):
        characters = string.ascii_letters + string.digits
        string_length = 28
        return "".join(secrets.choice(characters) for _ in range(string_length))

    def create_url(self):
        now = datetime.now()
        date = format_date_time(mktime(now.timetuple()))

        signature_origin = "host: " + self.host + "\n"
        signature_origin += "date: " + date + "\n"
        signature_origin += "GET " + self.path + " HTTP/1.1"

        print(signature_origin)
        signature_sha = hmac.new(
            self.api_secret.encode("utf-8"),
            signature_origin.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()

        signature_sha_base64 = base64.b64encode(signature_sha).decode(encoding="utf-8")

        authorization_origin = f'api_key="{self.api_key}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature_sha_base64}"'

        authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode(
            encoding="utf-8"
        )
        v = {"authorization": authorization, "date": date, "host": self.host}

        return self.spark_chat_url + "?" + urlencode(v)

    def on_error(self, ws, error):
        print("### error:", error)

    def on_close(self, ws, one, two):
        print(" ")

    def on_open(self, ws):
        thread.start_new_thread(self.run, (ws,))

    def run(self, ws, *args):
        params = self.generate_params(
            messages=ws.messages,
            functions=ws.functions,
            temperature=ws.temperature,
            max_tokens=ws.max_tokens,
        )
        print(params)
        data = json.dumps(params)
        ws.send(data)

    def on_message(self, ws, message):
        print(f"received message: {message}")
        data = json.loads(message)
        code = data["header"]["code"]
        if code != 0:
            print(f"请求错误: {code}, {data}")
            ws.close()
        else:
            choices = data["payload"]["choices"]
            status = choices["status"]
            content = choices["text"][0]["content"]
            # function_call
            if "function_call" in choices["text"][0]:
                function_call = choices["text"][0]["function_call"]
                self.function_call = function_call
            self.answer += content
            self.usage = data["payload"]["usage"]
            if status == 2:
                ws.close()

    def generate_params(self, messages, functions, temperature=0.7, max_tokens=2048):
        data = {
            "header": {"app_id": self.app_id, "uid": "verysmallwoods"},
            "parameter": {
                "chat": {
                    "domain": self.domain,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "auditing": "default",
                }
            },
            "payload": {
                "message": {"text": messages},
            },
        }
        if functions is not None and len(functions) > 0:
            data["payload"]["functions"] = {"text": functions}
        return data

    def chatCompletionStream(
        self, messages, functions, temperature=0.7, max_tokens=2048
    ):
        with connect(self.create_url()) as ws:
            params = self.generate_params(messages, functions, temperature, max_tokens)
            ws.send(json.dumps(params))

            thread_id = self.generate_random_id()
            while True:
                try:
                    message = ws.recv()
                    print(message)
                    data = json.loads(message)
                    code = data["header"]["code"]
                    if code != 0:
                        print(f"请求错误: {code}, {data}")
                        ws.close()
                        break
                    else:
                        payload = data["payload"]
                        choices = payload["choices"]
                        status = choices["status"]
                        content = choices["text"][0]["content"]

                        chunk = {
                            "id": f"chatcmpl-{thread_id}",
                            "object": "chat.completion.chunk",
                            "created": int(time()),
                            "model": "spark-ai",
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"role": "assistant", "content": content},
                                    "finish_reason": None,
                                }
                            ],
                        }

                        if len(content) > 0:
                            yield f"data: {json.dumps(chunk)}\n\n"

                        if status == 2:
                            # Completed with status 2
                            yield f"data: [DONE]\n\n"
                            break
                except ConnectionClosed:
                    print("Connection closed")
                    yield f"data: [DONE]\n\n"
                    break
        return

    def chatCompletion(self, messages, functions, temperature=0.7, max_tokens=2048):
        url = self.create_url()

        websocket.enableTrace(False)

        ws = websocket.WebSocketApp(
            url,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_open=self.on_open,
        )
        ws.messages = messages
        ws.temperature = temperature
        ws.max_tokens = max_tokens
        ws.functions = functions
        ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})

        completion = {
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": self.answer},
                    "finish_reason": "stop",
                }
            ],
            "usage": self.usage,
        }
        if self.function_call is not None:
            completion["choices"][0]["message"]["tool_calls"] = [
                {
                    "id": "tool-call-1",
                    "type": "function",
                    "function": self.function_call,
                }
            ]
            completion["choices"][0]["message"]["function_call"] = self.function_call
        return completion
