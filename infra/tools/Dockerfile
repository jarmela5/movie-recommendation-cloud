
# ---Base image---
FROM ubuntu:22.04

# Evita prompts interativos durante a instalação dos pacotes
ENV DEBIAN_FRONTEND=noninteractive



# ---Instalação das ferramentas base---
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ansible \
        ca-certificates \
        curl \
        gnupg \
        openssh-client



# ---Configuração do repositório HashiCorp---


# Diretório onde serão guardadas as chaves dos repositórios APT
RUN install -m 0755 -d /etc/apt/keyrings

# Importa e instala a chave GPG da HashiCorp
RUN curl -fsSL https://apt.releases.hashicorp.com/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/hashicorp-archive-keyring.gpg \
    && chmod a+r /etc/apt/keyrings/hashicorp-archive-keyring.gpg

# Adiciona o repositório oficial da HashiCorp
RUN echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/hashicorp-archive-keyring.gpg] \
https://apt.releases.hashicorp.com jammy main" \
        > /etc/apt/sources.list.d/hashicorp.list



# ---Configuração do repositório Kubernetes---


# Importa e instala a chave GPG do Kubernetes
RUN curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.28/deb/Release.key \
        | gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg \
    && chmod a+r /etc/apt/keyrings/kubernetes-apt-keyring.gpg

# Adiciona o repositório oficial do Kubernetes
RUN echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] \
https://pkgs.k8s.io/core:/stable:/v1.28/deb/ /" \
        > /etc/apt/sources.list.d/kubernetes.list


# ---Instalação das ferramentas de Infrastructure as Code---


# Atualiza os índices APT depois de adicionar os novos repositórios
# e instala Terraform e kubectl
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        terraform \
        kubectl



# ---Limpeza---


# Remove caches e listas do APT 
RUN apt-get clean \
    && rm -rf /var/lib/apt/lists/*


# ---Working directory---


WORKDIR /workspace/infra/gcp


# ---Comando por defeito---


CMD ["bash"]

