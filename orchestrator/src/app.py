import sys
import os
import threading

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")

# Fraud detection
fraud_detection_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/fraud_detection'))
sys.path.insert(0, fraud_detection_grpc_path)
import fraud_detection_pb2 as fraud_detection
import fraud_detection_pb2_grpc as fraud_detection_grpc

# Suggestions
suggestions_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/suggestions'))
sys.path.insert(0, suggestions_grpc_path)
import suggestions_pb2 as suggestions
import suggestions_pb2_grpc as suggestions_grpc

import grpc
from flask import Flask, request
from flask_cors import CORS
import json

app = Flask(__name__, template_folder="../../../frontend/src")
CORS(app, resources={r'/*': {'origins': '*'}})

@app.route('/', methods=['GET'])
def index():
    return "Orchestrator is running"

@app.route('/checkout', methods=['POST'])
def checkout():
    request_data = json.loads(request.data)
    items = request_data.get("items", {})
    print("Request Data:", request_data)

    results = {
        "is_fraud": False,
        "fraud_error": None,
        "suggestions": []
    }

    def check_fraud():
        try:
            with grpc.insecure_channel('fraud_detection:50051') as channel:
                stub = fraud_detection_grpc.FraudDetectionServiceStub(channel)
                for item in items:
                    response = stub.CheckFraud(fraud_detection.FraudRequest(
                        card_number=request_data.get("creditCard", {}).get("number"),
                        order_amount=item.get("quantity")
                    ))
                    if response.is_fraud:
                        results["is_fraud"] = True
                        results["fraud_error"] = "Fraud detected!"
                        return
        except Exception as e:
            print(f"Fraud detection error: {e}")

    def get_suggestions():
        try:
            with grpc.insecure_channel('suggestions:50053') as channel:
                stub = suggestions_grpc.SuggestionsServiceStub(channel)
                response = stub.GetSuggestions(suggestions.SuggestionRequest(
                    user_id=request_data.get("user", {}).get("name", "anonymous"),
                    ordered_items=[item.get("name", "") for item in items]
                ))
                results["suggestions"] = [
                    {"bookId": b.book_id, "title": b.title, "author": b.author}
                    for b in response.suggestions
                ]
        except Exception as e:
            print(f"Suggestions error: {e}")

    # Run both threads in parallel
    t1 = threading.Thread(target=check_fraud)
    t2 = threading.Thread(target=get_suggestions)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    if results["is_fraud"]:
        return {"status": "Order Rejected", "error": {"message": results["fraud_error"]}}, 400

    return {
        "orderId": "12345",
        "status": "Order Approved",
        "suggestedBooks": results["suggestions"]
    }

if __name__ == '__main__':
    app.run(host='0.0.0.0')