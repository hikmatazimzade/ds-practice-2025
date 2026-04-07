import sys
import os
from concurrent import futures

import numpy as np
from sklearn.ensemble import IsolationForest

import grpc

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
fraud_detection_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/fraud_detection'))
sys.path.insert(0, fraud_detection_grpc_path)

import fraud_detection_pb2 as pb
import fraud_detection_pb2_grpc as pb_grpc

MY_INDEX = 0  # [fraud=0, transaction=1, suggestions=2]
NUM_SERVICES = 3

orders_cache = {}  # { order_id: { "data": FraudInitRequest, "vc": [0,0,0] } }


def merge_and_increment(local_vc, incoming_vc):
    merged = [max(local_vc[i], incoming_vc[i]) for i in range(NUM_SERVICES)]
    merged[MY_INDEX] += 1
    return merged


def is_luhn_valid(card_number: str) -> bool:
    card_number = card_number.replace(" ", "").replace("-", "")
    if not card_number.isdigit():
        return False
    digits = [int(d) for d in card_number]
    odd_digits = digits[-1::-2]
    even_digits = [d * 2 for d in digits[-2::-2]]
    cal_digits = odd_digits + [d - 9 if d > 9 else d for d in even_digits]
    return sum(cal_digits) % 10 == 0


class FraudDetectionService(pb_grpc.FraudDetectionServiceServicer):
    def __init__(self):
        np.random.seed(42)
        retail = np.random.randint(1, 6, size=(800, 1))
        bulk = np.random.randint(10, 101, size=(200, 1))
        self.model = IsolationForest(contamination=0.01, random_state=42)
        self.model.fit(np.vstack((retail, bulk)))
        print("Fraud Service ready: Luhn + Isolation Forest trained.")

    def InitOrder(self, request, context):
        orders_cache[request.order_id] = {
            "data": request,
            "vc": [0] * NUM_SERVICES
        }
        print(f"[Fraud] InitOrder -> cached order {request.order_id}")
        return pb.FraudInitResponse(success=True)

    def CheckUserData(self, request, context):
        """Event (d): basic heuristics on name and contact."""
        order_id = request.order_id
        if order_id not in orders_cache:
            return pb.FraudCheckResponse(is_fraud=True, message="Order not found in cache")

        entry = orders_cache[order_id]
        entry["vc"] = merge_and_increment(entry["vc"], list(request.vector_clock))

        data = entry["data"]
        # Flag if name is empty or contact has no '@' (looks like a bot/invalid user)
        is_fraud = not (data.name.strip() and "@" in data.contact)
        msg = "User data looks suspicious" if is_fraud else "User data OK"
        print(f"[Fraud] CheckUserData order={order_id} is_fraud={is_fraud} VC={entry['vc']}")
        return pb.FraudCheckResponse(is_fraud=is_fraud, message=msg, vector_clock=entry["vc"])

    def CheckCardFraud(self, request, context):
        """Event (e): Luhn check + quantity anomaly detection."""
        order_id = request.order_id
        if order_id not in orders_cache:
            return pb.FraudCheckResponse(is_fraud=True, message="Order not found in cache")

        entry = orders_cache[order_id]
        entry["vc"] = merge_and_increment(entry["vc"], list(request.vector_clock))

        data = entry["data"]
        if not is_luhn_valid(data.card_number):
            print(f"[Fraud] CheckCardFraud order={order_id} -> invalid card (Luhn)")
            return pb.FraudCheckResponse(is_fraud=True, message="Invalid card number", vector_clock=entry["vc"])

        prediction = self.model.predict(np.array([[data.order_amount]]))
        is_fraud = prediction[0] == -1
        msg = "Anomalous order amount" if is_fraud else "Card OK"
        print(f"[Fraud] CheckCardFraud order={order_id} is_fraud={is_fraud} VC={entry['vc']}")
        return pb.FraudCheckResponse(is_fraud=is_fraud, message=msg, vector_clock=entry["vc"])


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    pb_grpc.add_FraudDetectionServiceServicer_to_server(FraudDetectionService(), server)
    server.add_insecure_port("[::]:50051")
    server.start()
    print("Fraud Detection Server started on port 50051.")
    server.wait_for_termination()


if __name__ == '__main__':
    serve()
