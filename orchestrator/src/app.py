import sys
import os
import uuid

from flask import render_template

# This set of lines are needed to import the gRPC stubs.
# The path of the stubs is relative to the current file, or absolute inside the container.
# Change these lines only if strictly needed.
FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
fraud_detection_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/fraud_detection'))
order_queue_grpc_path     = os.path.abspath(os.path.join(FILE, '../../../utils/pb/order_queue'))
sys.path.insert(0, fraud_detection_grpc_path)
sys.path.insert(0, order_queue_grpc_path)

import fraud_detection_pb2 as fraud_detection
import fraud_detection_pb2_grpc as fraud_detection_grpc
import order_queue_pb2 as order_queue_pb2
import order_queue_pb2_grpc as order_queue_grpc

import grpc

def greet(name='you'):
    # Establish a connection with the fraud-detection gRPC service.
    return f"Greeting {name}"


    # return "greeting"
    # with grpc.insecure_channel('fraud_detection:50051') as channel:
    #     # Create a stub object.
    #     stub = fraud_detection_grpc.HelloServiceStub(channel)
    #     # Call the service through the stub object.
    #     response = stub.SayHello(fraud_detection.HelloRequest(name=name))
    # return response.greeting

# Import Flask.
# Flask is a web framework for Python.
# It allows you to build a web application quickly.
# For more information, see https://flask.palletsprojects.com/en/latest/
from flask import Flask, request
from flask_cors import CORS
import json

# Create a simple Flask app.
app = Flask(__name__, template_folder="../../../frontend/src")
# Enable CORS for the app.
CORS(app, resources={r'/*': {'origins': '*'}})

# Define a GET endpoint.
@app.route('/', methods=['GET'])
def index():
    """
    Responds with 'Hello, [name]' when a GET request is made to '/' endpoint.
    """
    # Test the fraud-detection gRPC service.
    response = greet(name='orchestrator')
    # Return the response.
    return response

@app.route('/checkout', methods=['POST'])
def checkout():
    """
    Responds with a JSON object containing the order ID, status, and suggested books.
    """
    # Get request object data to json
    request_data = json.loads(request.data)
    items = request_data.get("items", {})

    # Print request object data
    print("Request Data:", request_data)

    with grpc.insecure_channel('fraud_detection:50051') as channel:
        # Create a stub object.
        stub = fraud_detection_grpc.FraudDetectionServiceStub(channel)
        # Call the service through the stub object.

        for item in items:
            response = stub.CheckFraud(fraud_detection.FraudRequest
                (card_number=request_data.get(
                "creditCard", {}).get("number"),
                order_amount=item.get("quantity")))

            if response.is_fraud:
                return {"status": "Order Rejected",
                        "error": {"message": "Fraud detected!"}}, 400

    # Build the Order protobuf message and enqueue it
    order_id = str(uuid.uuid4())
    order_items = [
        order_queue_pb2.OrderItem(name=item.get("name", ""), quantity=item.get("quantity", 1))
        for item in items
    ]
    order = order_queue_pb2.Order(
        order_id=order_id,
        items=order_items,
        user_name=request_data.get("userInfo", {}).get("name", ""),
        total_amount=sum(item.get("quantity", 1) for item in items),
    )

    with grpc.insecure_channel('order_queue:50055') as channel:
        queue_stub = order_queue_grpc.OrderQueueServiceStub(channel)
        enqueue_resp = queue_stub.Enqueue(order_queue_pb2.EnqueueRequest(order=order))
        if not enqueue_resp.success:
            return {"status": "Order Rejected", "error": {"message": "Failed to enqueue order."}}, 500
        print(f"Order enqueued: {enqueue_resp.order_id}")

    order_status_response = {"orderId": order_id, "status": "Order Approved",
                             "suggestedBooks": []}
    for idx, item in enumerate(items):
        order_status_response["suggestedBooks"].append({
            "bookId": idx + 1, "title": item["name"],
            "author": f"Author {idx + 1}"
        })

    return order_status_response


if __name__ == '__main__':
    # Run the app in debug mode to enable hot reloading.
    # This is useful for development.
    # The default port is 5000.
    app.run(host='0.0.0.0')
