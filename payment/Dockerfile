FROM python:3.9-slim

WORKDIR /app

COPY ./payment/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# On pointe vers le bon chemin du proto que j'ai vu sur ta photo
RUN python -m grpc_tools.protoc -I./utils/pb/payment --python_out=./payment/src --grpc_python_out=./payment/src ./utils/pb/payment/payment.proto

EXPOSE 50060

# Fais attention au chemin de l'app
CMD ["python", "payment/src/app.py"]