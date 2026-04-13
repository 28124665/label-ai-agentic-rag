# data-knowledge-api — Docker 镜像构建、Helm Chart 打包、镜像推送与 Kubernetes 部署
# 使用前请登录镜像仓库：docker login <registry>
# 部署前请配置 kubeconfig（export KUBECONFIG=... 或使用 deploy-boe）

.DEFAULT_GOAL := help

.PHONY: help clean dist docker-build docker-push build push build-push \
	helm-lint helm-template helm-package package \
	helm-deploy deploy upload deploy-boe build-push-deploy build-push-deploy-boe test

# ---- Docker（默认 Dockerfile-Honghai；覆盖示例：make build DOCKERFILE=Dockerfile）----
DOCKERFILE ?= Dockerfile-Honghai
IMAGE_REPO ?= infiniflow/data-knowledge-api
IMAGE_TAG ?= dev
# 推送目标仓库（镜像路径仍为 $(IMAGE_REPO)，TAG 仍为 $(IMAGE_TAG)）
REGISTRY_PUSH ?= 01ai-registry.cn-shanghai.cr.aliyuncs.com/01-ai
IMAGE_PUSH_REPO ?= $(REGISTRY_PUSH)/$(IMAGE_REPO)
DOCKER_BUILD_ARGS ?=

# ---- Helm ----
HELM_DIR := helm
HELM_RELEASE ?= ragflow
HELM_NAMESPACE ?= honhai
VALUES ?= $(HELM_DIR)/values.yaml
# 可选：额外 values，例如 make deploy VALUES_EXTRA=helm/my-prod.yaml
VALUES_EXTRA ?=
HELM_EXTRA_ARGS ?=

# BOE / 内网：本地 kubeconfig 路径（勿将含密钥的文件提交到 Git）
KUBECONFIG_BOE ?= $(CURDIR)/helm/.env.boe.yaml

# ---- Chart 打包输出 ----
DIST_DIR := dist

help: ## 显示可用目标
	@echo "data-knowledge-api — 打包 / 上传 / 部署"
	@echo ""
	@grep -E '^[a-zA-Z0-9_.-]+:.*##' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*##"}; {printf "  \033[36m%-28s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "常用示例:"
	@echo "  make build-push   # 默认 -f Dockerfile-Honghai；改用官方镜像: DOCKERFILE=Dockerfile"
	@echo "  make build-push IMAGE_REPO=infiniflow/data-knowledge-api IMAGE_TAG=v0.24.0"
	@echo "  # 默认推送至 REGISTRY_PUSH/IMAGE_REPO:IMAGE_TAG；可覆盖 REGISTRY_PUSH 或整段 IMAGE_PUSH_REPO"
	@echo "  make helm-deploy HELM_NAMESPACE=honhai KUBECONFIG=\$$HOME/.kube/config"
	@echo "  make build-push-deploy-boe   # 构建推送后使用 $(KUBECONFIG_BOE) 部署"
	@echo ""

dist: ## 创建 dist 目录
	@mkdir -p $(DIST_DIR)

clean: ## 删除 dist 打包产物
	@rm -rf $(DIST_DIR)

docker-build: ## 构建应用镜像
	@echo "Building $(IMAGE_REPO):$(IMAGE_TAG) (Dockerfile=$(DOCKERFILE))"
	DOCKER_BUILDKIT=1 docker build $(DOCKER_BUILD_ARGS) -f $(DOCKERFILE) -t $(IMAGE_REPO):$(IMAGE_TAG) .

docker-push: ## 推送镜像到仓库（打 tag 到 $(IMAGE_PUSH_REPO):$(IMAGE_TAG) 后推送）
	@echo "Pushing $(IMAGE_PUSH_REPO):$(IMAGE_TAG)"
	docker tag $(IMAGE_REPO):$(IMAGE_TAG) $(IMAGE_PUSH_REPO):$(IMAGE_TAG)
	docker push $(IMAGE_PUSH_REPO):$(IMAGE_TAG)

build: docker-build ## 同 docker-build
push: docker-push ## 同 docker-push

build-push: docker-build docker-push ## 构建并推送镜像

helm-lint: ## 校验 Helm Chart
	helm lint $(HELM_DIR)

helm-template: helm-lint ## 渲染模板（检查用，不安装）
	helm template $(HELM_RELEASE) $(HELM_DIR) \
		-n $(HELM_NAMESPACE) \
		-f $(VALUES) \
		$(if $(VALUES_EXTRA),-f $(VALUES_EXTRA),) \
		--set data-knowledge-api.image.repository=$(IMAGE_PUSH_REPO) \
		--set data-knowledge-api.image.tag=$(IMAGE_TAG) \
		$(HELM_EXTRA_ARGS)

helm-package: dist helm-lint ## 将 Chart 打包为 tgz 到 dist/
	helm package $(HELM_DIR) -d $(DIST_DIR)/
	@echo "Chart package written under $(DIST_DIR)/"

package: helm-package ## 打包 Helm Chart（产物在 dist/）

upload: docker-push ## 上传镜像到镜像仓库

helm-deploy: helm-lint ## 使用 Helm 安装/升级集群中的 release（使用当前 kubectl 上下文或 KUBECONFIG）
	helm upgrade --install $(HELM_RELEASE) $(HELM_DIR) \
		-n $(HELM_NAMESPACE) --create-namespace \
		-f $(VALUES) \
		$(if $(VALUES_EXTRA),-f $(VALUES_EXTRA),) \
		--set data-knowledge-api.image.repository=$(IMAGE_PUSH_REPO) \
		--set data-knowledge-api.image.tag=$(IMAGE_TAG) \
		$(HELM_EXTRA_ARGS)

deploy: helm-deploy ## 同 helm-deploy

deploy-boe: ## 使用 KUBECONFIG_BOE 指向的 kubeconfig 部署（默认 helm/.env.boe.yaml）
	@test -f "$(KUBECONFIG_BOE)" || (echo "缺少 kubeconfig: $(KUBECONFIG_BOE)"; exit 1)
	KUBECONFIG="$(KUBECONFIG_BOE)" $(MAKE) helm-deploy

build-push-deploy: build-push helm-deploy ## 构建、推送镜像并 Helm 部署

build-push-deploy-boe: build-push deploy-boe ## 构建、推送镜像并使用 BOE kubeconfig 部署

test: ## 运行后端 pytest（需 uv）
	uv run pytest
