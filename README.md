# GPU Container Exporter

> gpu-container-exporter

Docker 컨테이너별 GPU 사용량을 추적하는 Custom Prometheus Exporter입니다.

## 기능

이 Exporter는 다음을 수행합니다:

1. **nvidia-smi**에서 GPU 프로세스 정보 수집 (PID, GPU 사용률, 메모리 사용률)
2. Docker API를 통해 각 컨테이너의 PID 목록 수집
3. PID를 매칭하여 **컨테이너별 GPU 사용량** 계산
4. Prometheus 메트릭으로 노출 (포트 9500)

## 제공 메트릭

### Container별 메트릭

#### 1. gpu_container_utilization_percent
컨테이너의 GPU 사용률 (SM utilization, %)

**레이블:**
- `container_name`: 컨테이너 이름
- `container_id`: 컨테이너 ID (12자리)
- `gpu_id`: GPU ID (0, 1, 2, ...)

**예시:**
```
gpu_container_utilization_percent{container_name="inference-worker",container_id="abc123def456",gpu_id="0"} 85
```

#### 2. gpu_container_memory_utilization_percent
컨테이너의 GPU 메모리 사용률 (%)

**레이블:** 위와 동일

#### 3. gpu_container_memory_used_mib
컨테이너가 사용하는 GPU 메모리 (MiB)

**레이블:** 위와 동일

**예시:**
```
gpu_container_memory_used_mib{container_name="inference-worker",container_id="abc123def456",gpu_id="0"} 4096
```

#### 4. gpu_container_process_count
컨테이너가 실행 중인 GPU 프로세스 수

**레이블:** 위와 동일

### GPU 레벨 메트릭 (GPU ID별)

#### 5. gpu_memory_total_mib
GPU의 총 메모리 용량 (MiB)

**레이블:**
- `gpu_id`: GPU ID (0, 1, 2, ...)
- `gpu_name`: GPU 모델명 (예: "NVIDIA A100-SXM4-40GB")
- `gpu_uuid`: GPU UUID

**예시:**
```
gpu_memory_total_mib{gpu_id="0",gpu_name="NVIDIA A100-SXM4-40GB",gpu_uuid="GPU-xxx"} 40960
```

#### 6. gpu_memory_used_total_mib
GPU의 현재 사용 중인 총 메모리 (MiB)

**레이블:** 위와 동일

**예시:**
```
gpu_memory_used_total_mib{gpu_id="0",gpu_name="NVIDIA A100-SXM4-40GB",gpu_uuid="GPU-xxx"} 8192
```

#### 7. gpu_utilization_total_percent
GPU의 전체 사용률 (%)

**레이블:** 위와 동일

**예시:**
```
gpu_utilization_total_percent{gpu_id="0",gpu_name="NVIDIA A100-SXM4-40GB",gpu_uuid="GPU-xxx"} 75
```

## 설치 및 실행

### 1. Docker Compose로 실행

```bash
cd Setup/service-prometheus-grafana/exporters/gpu-container-exporter
docker-compose up -d
```

### 2. 메트릭 확인

```bash
curl http://localhost:9500/metrics
```

**예시 출력:**
```prometheus
# HELP gpu_container_utilization_percent GPU utilization per container
# TYPE gpu_container_utilization_percent gauge
gpu_container_utilization_percent{container_name="daq_inference_worker",container_id="abc123def456",gpu_id="0"} 85.0

# HELP gpu_container_memory_used_mib GPU memory used by container in MiB
# TYPE gpu_container_memory_used_mib gauge
gpu_container_memory_used_mib{container_name="daq_inference_worker",container_id="abc123def456",gpu_id="0"} 4096.0

# HELP gpu_container_process_count Number of GPU processes per container
# TYPE gpu_container_process_count gauge
gpu_container_process_count{container_name="daq_inference_worker",container_id="abc123def456",gpu_id="0"} 2.0
```

### 3. Prometheus 설정

`Setup/service-prometheus-grafana/prometheus/metric-config/default/prometheus.yml`에 추가:

```yaml
scrape_configs:
  - job_name: 'gpu-container-exporter'
    honor_labels: true
    honor_timestamps: true
    scheme: 'http'
    scrape_interval: 10s
    static_configs:
      - targets: ['localhost:9500']
        labels:
          service: 'gpu-container'
```

또는 file_sd_configs 사용:
```yaml
scrape_configs:
  - job_name: 'gpu-container-exporter'
    honor_labels: true
    honor_timestamps: true
    scheme: 'http'
    scrape_interval: 10s
    file_sd_configs:
      - files:
        - './targets/gpu-container-exporter.json'
```

`targets/gpu-container-exporter.json`:
```json
[{"targets":["localhost:9500"],"labels":{"service":"gpu-container"}}]
```

## 작동 원리

```
1. nvidia-smi pmon 실행
   ↓
   GPU PID 목록 획득 (PID 1234: GPU 0, 85% 사용)

2. Docker API로 모든 컨테이너 조회
   ↓
   각 컨테이너의 PID 목록 획득

3. PID 매칭
   ↓
   PID 1234 → 컨테이너 "inference-worker"

4. Prometheus 메트릭 생성
   ↓
   gpu_container_utilization_percent{container_name="inference-worker",gpu_id="0"} 85
```

## 필수 권한

Exporter가 정상 작동하려면 다음 권한이 필요합니다:

1. **Docker socket 접근**: 컨테이너 정보 수집
   ```yaml
   volumes:
     - /var/run/docker.sock:/var/run/docker.sock:ro
   ```

2. **Host PID namespace**: 컨테이너 PID 매칭
   ```yaml
   pid: host
   ```

3. **GPU 접근**: nvidia-smi 실행
   ```yaml
   deploy:
     resources:
       reservations:
         devices:
           - driver: nvidia
             capabilities: [utility]
   ```