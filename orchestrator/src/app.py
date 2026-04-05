import sys
import os
import threading
import grpc
import json
import uuid
from flask import Flask, request
from flask_cors import CORS

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")

utils_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb'))
sys.path.insert(0, os.path.join(utils_path, 'fraud_detection'))
sys.path.insert(0, os.path.join(utils_path, 'suggestions'))
sys.path.insert(0, os.path.join(utils_path, 'transaction_verification'))

import fraud_detection_pb2 as fraud_detection
import fraud_detection_pb2_grpc as fraud_detection_grpc
import suggestions_pb2 as suggestions
import suggestions_pb2_grpc as suggestions_grpc
import transaction_verification_pb2 as transaction_verification
import transaction_verification_pb2_grpc as transaction_verification_grpc

app = Flask(__name__, template_folder="../../../frontend/src")
CORS(app, resources={r'/*': {'origins': '*'}})

@app.route('/', methods=['GET'])
def index():
    return "Orchestrator is running"

@app.route('/checkout', methods=['POST'])
def checkout():
    request_data = json.loads(request.data)
    print("Request Data:", request_data)

    user = request_data.get('user', {})
    card_info = request_data.get('creditCard', {})
    addr_info = request_data.get('billingAddress', {})
    items = request_data.get('items', [])

    # Generate unique order ID
    order_id = str(uuid.uuid4())
    print(f"[Orchestrator] Generated OrderID: {order_id}")

    results = {
        "is_fraud": False,
        "fraud_error": None,
        "is_valid": True,
        "transaction_error": None,
        "suggestions": []
    }

    # --- Thread 1: Fraud Detection ---
    def check_fraud():
        try:
            with grpc.insecure_channel('fraud_detection:50051') as channel:
                stub = fraud_detection_grpc.FraudDetectionServiceStub(channel)
                response = stub.CheckFraud(fraud_detection.FraudRequest(
                    card_number=card_info.get('number', ''),
                    order_amount=float(len(items) * 30)
                ))
                if response.is_fraud:
                    results["is_fraud"] = True
                    results["fraud_error"] = "Fraud detected!"
        except Exception as e:
            print(f"[Orchestrator] Fraud detection error: {e}")

    # --- Thread 2: Transaction Verification ---
    def verify_transaction():
        try:
            with grpc.insecure_channel('transaction_verification:50052') as channel:
                stub = transaction_verification_grpc.TransactionVerificationServiceStub(channel)
                response = stub.TransactionVerification(
                    transaction_verification.TransactionVerificationRequest(
                        name=user.get('name', ''),
                        contact=user.get('contact', ''),
                        creditCard=card_info.get('number', ''),
                        itemsCount=len(items),
                        cvv=card_info.get('cvv', ''),
                        street=addr_info.get('street', ''),
                        city=addr_info.get('city', ''),
                        zip=addr_info.get('zip', ''),
                        country=addr_info.get('country', ''),
                        state=addr_info.get('state', '')
                    )
                )
                if not response.is_valid:
                    results["is_valid"] = False
                    results["transaction_error"] = response.message
        except Exception as e:
            print(f"[Orchestrator] Transaction verification error: {e}")

    # --- Thread 3: Suggestions ---
    def get_suggestions():
        try:
            with grpc.insecure_channel('suggestions:50053') as channel:
                stub = suggestions_grpc.SuggestionsServiceStub(channel)
                response = stub.GetSuggestions(suggestions.SuggestionRequest(
                    user_id=user.get('name', 'anonymous'),
                    ordered_items=[i.get('name', '') for i in items]
                ))
                results["suggestions"] = [
                    {"bookId": b.book_id, "title": b.title, "author": b.author}
                    for b in response.suggestions
                ]
        except Exception as e:
            print(f"[Orchestrator] Suggestions error: {e}")

    # --- Run all 3 threads in parallel ---
    print(f"[Orchestrator] Spawning 3 worker threads for OrderID: {order_id}")
    t1 = threading.Thread(target=check_fraud)
    t2 = threading.Thread(target=verify_transaction)
    t3 = threading.Thread(target=get_suggestions)

    t1.start()
    t2.start()
    t3.start()

    t1.join()
    t2.join()
    t3.join()

    print(f"[Orchestrator] All threads finished for OrderID: {order_id}")

    # --- Consolidate results ---
    if not results["is_valid"]:
        return {"orderId": order_id, "status": "Order Rejected",
                "error": {"message": results["transaction_error"]}, "suggestedBooks": []}, 400

    if results["is_fraud"]:
        return {"orderId": order_id, "status": "Order Rejected",
                "error": {"message": results["fraud_error"]}, "suggestedBooks": []}, 400

    return {
        "orderId": order_id,
        "status": "Order Approved",
        "suggestedBooks": results["suggestions"]
    }

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)