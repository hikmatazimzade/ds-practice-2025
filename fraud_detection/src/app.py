import sys
import os
from concurrent import futures

import numpy as np
from sklearn.ensemble import IsolationForest

import grpc

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
fraud_detection_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/fraud_detection'))
sys.path.insert(0, fraud_detection_grpc_path)

import fraud_detection_pb2 as fraud_detection
import fraud_detection_pb2_grpc as fraud_detection_grpc


def is_luhn_valid(card_number: str) -> bool:
    """
    Checks if a card number is mathematically valid using the Luhn algorithm.
    """
    card_number = card_number.replace(" ", "").replace("-", "")

    if not card_number.isdigit():
        return False

    digits = [int(d) for d in str(card_number)]
    odd_digits = digits[-1::-2]
    even_digits = [d * 2 for d in digits[-2::-2]]

    cal_digits = odd_digits + [d - 9 if d > 9 else d for d in even_digits]

    return sum(cal_digits) % 10 == 0


class FraudDetectionService(fraud_detection_grpc.FraudDetectionServiceServicer):
    def __init__(self):
        np.random.seed(42)

        # Simulate 800 standard retail purchases (1 to 5 books)
        retail_purchases = np.random.randint(1, 6, size=(800, 1))

        # Simulate 200 bulk/school/library purchases (10 to 100 books)
        bulk_purchases = np.random.randint(10, 101, size=(200, 1))

        # Combine them into our training dataset
        normal_quantities = np.vstack((retail_purchases, bulk_purchases))

        # We tell it to expect outliers by setting contamination.
        self.model = IsolationForest(contamination=0.01, random_state=42)
        self.model.fit(normal_quantities)

        print("Fraud Service Ready: Luhn Algorithm active & Isolation Forest trained on quantities.")

    def CheckFraud(self, request, context):
        print(f"Evaluating order -> Card: {request.card_number}, Quantity: {request.order_amount}")

        response = fraud_detection.FraudResponse()

        # Check the card structurally
        if not is_luhn_valid(request.card_number):
            print("--> Result: FRAUD DETECTED (Invalid Card Structure)")
            response.is_fraud = True
            return response

        # Check the quantity behaviorally using AI
        feature_vector = np.array([[request.order_amount]])
        ai_prediction = self.model.predict(feature_vector)

        # The model returns 1 for normal, -1 for anomalies
        if ai_prediction[0] == -1:
            print(f"--> Result: FRAUD DETECTED (Anomalous Quantity: {request.order_amount})")
            response.is_fraud = True
        else:
            print("--> Result: Transaction Approved")
            response.is_fraud = False

        return response


def serve():
    server = grpc.server(futures.ThreadPoolExecutor())

    fraud_detection_grpc.add_FraudDetectionServiceServicer_to_server(
        FraudDetectionService(), server)

    port = "50051"
    server.add_insecure_port("[::]:" + port)

    server.start()
    print(f"Fraud Detection Server started. Listening on port {port}.")
    server.wait_for_termination()


if __name__ == '__main__':
    serve()