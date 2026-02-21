import sys
import os
from concurrent import futures

import grpc


FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
fraud_detection_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/fraud_detection'))
sys.path.insert(0, fraud_detection_grpc_path)

import fraud_detection_pb2 as fraud_detection
import fraud_detection_pb2_grpc as fraud_detection_grpc


class FraudDetectionService(fraud_detection_grpc.FraudDetectionServiceServicer):

    def CheckFraud(self, request, context):
        print(f"Checking fraud for card: {request.card_number}, "
              f"amount: {request.order_amount}")

        response = fraud_detection.FraudResponse()
        if (request.card_number.startswith("999") or
                                    request.order_amount > 1000):
            response.is_fraud = True

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