#!/bin/bash
set -e

# Quick deployment script for Wasabi S3 Operator

NAMESPACE="wasabi-s3-operator-system"
IMAGE_NAME="kenchrcum/wasabi-s3-operator"
VERSION="${1:-latest}"

echo "🚀 Deploying Wasabi S3 Operator..."
echo ""

# Step 1: Build image
echo "📦 Building Docker image..."
docker build -t "${IMAGE_NAME}:${VERSION}" .

echo "✅ Build complete!"
echo ""

# Step 2: Check if we should push
read -p "Push image to Docker Hub? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "📤 Pushing image..."
    docker push "${IMAGE_NAME}:${VERSION}"
    echo "✅ Push complete!"
fi

echo ""

# Step 3: Install operator
echo "🔧 Installing operator..."
helm install wasabi-s3-operator ./helm/wasabi-s3-operator \
  --namespace "${NAMESPACE}" \
  --create-namespace \
  --set image.repository="${IMAGE_NAME}" \
  --set image.tag="${VERSION}" \
  --wait

echo "✅ Installation complete!"
echo ""

# Step 4: Verify
echo "🔍 Verifying installation..."
kubectl get deployment -n "${NAMESPACE}"
kubectl get pods -n "${NAMESPACE}"
kubectl get crd | grep s3.cloud37.dev

echo ""
echo "🎉 Wasabi S3 Operator is ready!"
echo ""
echo "Next steps:"
echo "  1. Create a Provider: kubectl apply -f examples/provider-wasabi.yaml"
echo "  2. Create a Bucket: kubectl apply -f examples/bucket-basic.yaml"
echo "  3. Check logs: kubectl logs -n ${NAMESPACE} -l app.kubernetes.io/name=wasabi-s3-operator"

