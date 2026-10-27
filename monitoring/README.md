# Monitoring

O projeto usa Prometheus para recolher métricas HTTP e Grafana para as apresentar num dashboard. Este diretório contém a configuração versionada:

```text
monitoring/
├── prometheus.yml
└── grafana/
    ├── datasource.yml
    ├── dashboards.yml
    └── dashboard.json
```

## Componentes monitorizados

Os serviços `user`, `catalog` e `recommendations` expõem `/metrics` na porta `8000`. O frontend é servido por Nginx e não expõe métricas Prometheus próprias; os pedidos que encaminha aparecem nas métricas dos backends.

| Serviço | Instrumentação | Exemplos |
| --- | --- | --- |
| User | `prometheus_flask_exporter` | `flask_http_request_total`, `flask_http_request_duration_seconds` |
| Catalog | `prometheus-fastapi-instrumentator` | `http_requests_total`, `http_request_duration_seconds` |
| Recommendations | `prometheus-fastapi-instrumentator` | `http_requests_total`, `http_request_duration_seconds` |

O dashboard apresenta estado dos serviços (`up`), taxa de pedidos, latência p95 e taxa de erros HTTP 5xx.

## Docker Compose

Os containers de monitoring arrancam juntamente com a aplicação:

```bash
docker compose up -d --build
```

Endereços locais:

- Prometheus: <http://localhost:9090>
- Grafana: <http://localhost:3000>
- Dashboard: <http://localhost:3000/d/g36-overview/g36-movie-recommendation-hub>

O datasource e o dashboard são provisionados automaticamente a partir dos ficheiros deste diretório. O Grafana permite visualização anónima para simplificar a demonstração; a password administrativa local é `admin`.

## Verificar o sistema

```bash
docker compose ps
curl -s -o /dev/null -w "app        -> %{http_code}\n" http://localhost:8080/
curl -s -o /dev/null -w "prometheus -> %{http_code}\n" http://localhost:9090/-/ready
curl -s -o /dev/null -w "grafana    -> %{http_code}\n" http://localhost:3000/api/health
```

Confirmar os targets descobertos:

```bash
curl -s "http://localhost:9090/api/v1/targets?state=active"
```

Devem existir targets para `user`, `catalog` e `recommendations` (além do próprio Prometheus, quando configurado). A página <http://localhost:9090/targets> apresenta o estado de forma mais legível.

Para produzir tráfego e observar os gráficos:

```bash
for i in $(seq 1 2000); do
  curl -s -o /dev/null "http://localhost:8080/api/movies?page=1"
  curl -s -o /dev/null "http://localhost:8080/api/user/profiles"
done
```

Queries úteis na API do Prometheus:

```text
sum by (service) (rate(http_requests_total[1m]))
sum by (service) (rate(flask_http_request_total[1m]))
histogram_quantile(0.95, sum by (service, le) (rate(http_request_duration_seconds_bucket[5m])))
```

As métricas de taxa usam janelas temporais. Por isso, a taxa pode voltar a zero pouco depois de o tráfego parar; os contadores totais continuam disponíveis.

## Kubernetes

No cluster, Prometheus usa a API Kubernetes para descobrir pods no namespace `movie-app` e limita a descoberta aos serviços `user`, `catalog` e `recommendations`. A sua ServiceAccount tem permissões read-only e limitadas ao namespace.

Os endpoints são expostos por NodePort:

| Serviço | NodePort |
| --- | ---: |
| Grafana | `30030` |
| Prometheus | `30090` |

Exemplo, substituindo `<NODE_IP>` pelo IP de um nó:

```bash
curl -s -o /dev/null -w "grafana -> %{http_code}\n" http://<NODE_IP>:30030/api/health
curl -s "http://<NODE_IP>:30090/api/v1/targets?state=active"
```

O Prometheus usa `emptyDir` com retenção de seis horas. O histórico perde-se quando o pod é recriado; para produção seria necessário configurar um `PersistentVolumeClaim` e uma política de retenção adequada.

## Troubleshooting

- Se um target estiver `down`, confirmar `docker compose ps` ou `kubectl -n movie-app get pods,svc`.
- Confirmar que `/metrics` responde dentro da rede do deployment.
- Em Kubernetes, verificar as permissões da ServiceAccount e os endpoints descobertos.
- Se o dashboard estiver vazio, confirmar que o datasource aponta para `http://prometheus:9090` e que existem targets ativos.
- Se a taxa estiver a zero, gerar tráfego enquanto o dashboard está aberto; as queries usam janelas de um a cinco minutos.

Para a configuração do deployment e do cluster, consultar [`../infra/README.md`](../infra/README.md) e [`../k8s/README.md`](../k8s/README.md).
