#!/usr/bin/env python3
"""
GPU Container Exporter
Tracks GPU usage per Docker container by mapping nvidia-smi PIDs to container PIDs
"""

import time
import subprocess
import re
import logging
from collections import defaultdict
from prometheus_client import start_http_server, Gauge, Info
import docker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Prometheus metrics
gpu_utilization = Gauge(
    'gpu_container_utilization_percent',
    'GPU utilization per container',
    ['container_name', 'container_id', 'gpu_id']
)

gpu_memory_utilization = Gauge(
    'gpu_container_memory_utilization_percent',
    'GPU memory utilization per container',
    ['container_name', 'container_id', 'gpu_id']
)

gpu_memory_used = Gauge(
    'gpu_container_memory_used_mib',
    'GPU memory used by container in MiB',
    ['container_name', 'container_id', 'gpu_id']
)

container_gpu_info = Info(
    'gpu_container_info',
    'GPU container information',
    ['container_name', 'container_id', 'gpu_id']
)

gpu_process_count = Gauge(
    'gpu_container_process_count',
    'Number of GPU processes per container',
    ['container_name', 'container_id', 'gpu_id']
)

# GPU-level metrics (total usage per GPU)
gpu_memory_total_mib = Gauge(
    'gpu_memory_total_mib',
    'Total GPU memory capacity in MiB',
    ['gpu_id', 'gpu_name', 'gpu_uuid']
)

gpu_memory_used_total_mib = Gauge(
    'gpu_memory_used_total_mib',
    'Total GPU memory currently used in MiB',
    ['gpu_id', 'gpu_name', 'gpu_uuid']
)

gpu_utilization_total_percent = Gauge(
    'gpu_utilization_total_percent',
    'Overall GPU utilization percentage',
    ['gpu_id', 'gpu_name', 'gpu_uuid']
)


class GPUContainerExporter:
    def __init__(self):
        try:
            # Try to connect to Docker using unix socket explicitly
            self.docker_client = docker.DockerClient(base_url='unix:///var/run/docker.sock')
            self.docker_client.ping()
            logger.info("GPU Container Exporter initialized with unix socket")
        except Exception as e:
            logger.error(f"Failed to connect to Docker with unix socket: {e}")
            logger.info("Trying alternative Docker connection method...")
            try:
                self.docker_client = docker.from_env()
                logger.info("GPU Container Exporter initialized with from_env()")
            except Exception as e2:
                logger.error(f"Failed to initialize Docker client: {e2}")
                raise

    def get_nvidia_smi_processes(self):
        """
        Get GPU processes from nvidia-smi
        Returns: dict of {pid: {'gpu_id': str, 'sm': int, 'mem': int, 'command': str}}
        """
        try:
            # nvidia-smi pmon returns: gpu, pid, type, sm, mem, enc, dec, command
            result = subprocess.run(
                ['nvidia-smi', 'pmon', '-c', '1', '-s', 'um'],
                capture_output=True,
                text=True,
                timeout=5
            )

            processes = {}
            for line in result.stdout.split('\n'):
                # Skip header and empty lines
                if line.startswith('#') or not line.strip():
                    continue

                # Parse line: gpu pid type sm mem enc dec command
                parts = line.split()
                if len(parts) < 5:
                    continue

                try:
                    gpu_id = parts[0]
                    pid = int(parts[1])
                    sm = int(parts[3]) if parts[3] != '-' else 0
                    mem = int(parts[4]) if parts[4] != '-' else 0
                    command = parts[7] if len(parts) > 7 else 'unknown'

                    processes[pid] = {
                        'gpu_id': gpu_id,
                        'sm': sm,
                        'mem': mem,
                        'command': command
                    }
                except (ValueError, IndexError) as e:
                    logger.debug(f"Failed to parse line: {line}, error: {e}")
                    continue

            return processes
        except subprocess.TimeoutExpired:
            logger.error("nvidia-smi timeout")
            return {}
        except FileNotFoundError:
            logger.error("nvidia-smi not found")
            return {}
        except Exception as e:
            logger.error(f"Error running nvidia-smi: {e}")
            return {}

    def get_gpu_memory_info(self):
        """
        Get per-process GPU memory usage from nvidia-smi
        Returns: dict of {pid: memory_mib}
        """
        try:
            result = subprocess.run(
                ['nvidia-smi', '--query-compute-apps=pid,used_memory', '--format=csv,noheader,nounits'],
                capture_output=True,
                text=True,
                timeout=5
            )

            memory_info = {}
            for line in result.stdout.strip().split('\n'):
                if not line.strip():
                    continue
                parts = line.split(',')
                if len(parts) >= 2:
                    try:
                        pid = int(parts[0].strip())
                        memory_mib = int(parts[1].strip())
                        memory_info[pid] = memory_mib
                    except ValueError:
                        continue

            return memory_info
        except Exception as e:
            logger.error(f"Error getting GPU memory info: {e}")
            return {}

    def get_gpu_info(self):
        """
        Get GPU information (total memory, used memory, utilization)
        Returns: dict of {gpu_id: {'name': str, 'uuid': str, 'memory_total': int, 'memory_used': int, 'utilization': int}}
        """
        try:
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=index,name,uuid,memory.total,memory.used,utilization.gpu',
                 '--format=csv,noheader,nounits'],
                capture_output=True,
                text=True,
                timeout=5
            )

            gpu_info = {}
            for line in result.stdout.strip().split('\n'):
                if not line.strip():
                    continue
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 6:
                    try:
                        gpu_id = parts[0]
                        name = parts[1]
                        uuid = parts[2]
                        memory_total = int(parts[3])
                        memory_used = int(parts[4])
                        utilization = int(parts[5])

                        gpu_info[gpu_id] = {
                            'name': name,
                            'uuid': uuid,
                            'memory_total': memory_total,
                            'memory_used': memory_used,
                            'utilization': utilization
                        }
                    except ValueError as e:
                        logger.debug(f"Failed to parse GPU info line: {line}, error: {e}")
                        continue

            return gpu_info
        except Exception as e:
            logger.error(f"Error getting GPU info: {e}")
            return {}

    def get_container_pids(self, container):
        """
        Get all PIDs running in a container
        """
        try:
            # Get top output from container
            top = container.top(ps_args='aux')
            pids = []
            for process in top['Processes']:
                # PID is usually the second column
                if len(process) > 1:
                    try:
                        pid = int(process[1])
                        pids.append(pid)
                    except ValueError:
                        continue
            return pids
        except Exception as e:
            logger.debug(f"Error getting PIDs for container {container.name}: {e}")
            return []

    def map_pids_to_containers(self):
        """
        Create a mapping of PID to container information
        Returns: dict of {pid: {'container_name': str, 'container_id': str}}
        """
        pid_to_container = {}

        try:
            containers = self.docker_client.containers.list()
            for container in containers:
                container_pids = self.get_container_pids(container)
                for pid in container_pids:
                    pid_to_container[pid] = {
                        'container_name': container.name,
                        'container_id': container.id[:12]
                    }
        except Exception as e:
            logger.error(f"Error mapping PIDs to containers: {e}")

        return pid_to_container

    def collect_metrics(self):
        """
        Collect GPU metrics per container
        """
        try:
            # Get GPU-level information
            gpu_info = self.get_gpu_info()

            # Get GPU processes from nvidia-smi
            gpu_processes = self.get_nvidia_smi_processes()
            gpu_memory_info = self.get_gpu_memory_info()

            # Get container PID mapping
            pid_to_container = self.map_pids_to_containers()

            # Aggregate metrics by container and GPU
            container_metrics = defaultdict(lambda: defaultdict(lambda: {
                'sm_sum': 0,
                'mem_sum': 0,
                'memory_used': 0,
                'process_count': 0
            }))

            # Map GPU processes to containers
            for pid, gpu_process_info in gpu_processes.items():
                if pid in pid_to_container:
                    container_info = pid_to_container[pid]
                    container_name = container_info['container_name']
                    container_id = container_info['container_id']
                    gpu_id = gpu_process_info['gpu_id']

                    key = (container_name, container_id)
                    container_metrics[key][gpu_id]['sm_sum'] += gpu_process_info['sm']
                    container_metrics[key][gpu_id]['mem_sum'] += gpu_process_info['mem']
                    container_metrics[key][gpu_id]['process_count'] += 1

                    # Add memory usage if available
                    if pid in gpu_memory_info:
                        container_metrics[key][gpu_id]['memory_used'] += gpu_memory_info[pid]

            # Update Prometheus metrics
            # Reset all metrics first
            gpu_utilization._metrics.clear()
            gpu_memory_utilization._metrics.clear()
            gpu_memory_used._metrics.clear()
            gpu_process_count._metrics.clear()
            gpu_memory_total_mib._metrics.clear()
            gpu_memory_used_total_mib._metrics.clear()
            gpu_utilization_total_percent._metrics.clear()

            # Set GPU-level metrics
            for gpu_id, info in gpu_info.items():
                gpu_memory_total_mib.labels(
                    gpu_id=gpu_id,
                    gpu_name=info['name'],
                    gpu_uuid=info['uuid']
                ).set(info['memory_total'])

                gpu_memory_used_total_mib.labels(
                    gpu_id=gpu_id,
                    gpu_name=info['name'],
                    gpu_uuid=info['uuid']
                ).set(info['memory_used'])

                gpu_utilization_total_percent.labels(
                    gpu_id=gpu_id,
                    gpu_name=info['name'],
                    gpu_uuid=info['uuid']
                ).set(info['utilization'])

            # Set container-level metrics
            for (container_name, container_id), gpu_data in container_metrics.items():
                for gpu_id, metrics in gpu_data.items():
                    gpu_utilization.labels(
                        container_name=container_name,
                        container_id=container_id,
                        gpu_id=gpu_id
                    ).set(metrics['sm_sum'])

                    gpu_memory_utilization.labels(
                        container_name=container_name,
                        container_id=container_id,
                        gpu_id=gpu_id
                    ).set(metrics['mem_sum'])

                    gpu_memory_used.labels(
                        container_name=container_name,
                        container_id=container_id,
                        gpu_id=gpu_id
                    ).set(metrics['memory_used'])

                    gpu_process_count.labels(
                        container_name=container_name,
                        container_id=container_id,
                        gpu_id=gpu_id
                    ).set(metrics['process_count'])

            logger.info(f"Collected metrics for {len(container_metrics)} containers")

        except Exception as e:
            logger.error(f"Error collecting metrics: {e}")

    def run(self, port=9500, interval=10):
        """
        Start the exporter
        """
        start_http_server(port)
        logger.info(f"GPU Container Exporter started on port {port}")

        while True:
            try:
                self.collect_metrics()
                time.sleep(interval)
            except KeyboardInterrupt:
                logger.info("Shutting down...")
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(interval)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='GPU Container Exporter')
    parser.add_argument('--port', type=int, default=9500, help='Port to expose metrics (default: 9500)')
    parser.add_argument('--interval', type=int, default=10, help='Collection interval in seconds (default: 10)')
    args = parser.parse_args()

    exporter = GPUContainerExporter()
    exporter.run(port=args.port, interval=args.interval)
