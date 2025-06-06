import os
import sys
import time
import threading
import random
import statistics
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple
import queue

# Agregar el directorio actual al path para importar módulos
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import chatbot
from logger_config import logger
from llm_pool import llm_pool

class EstressTester:
    def __init__(self, num_usuarios: int = 18, preguntas_por_usuario: int = 10, duracion_minutos: int = 10):
        self.num_usuarios = num_usuarios
        self.preguntas_por_usuario = preguntas_por_usuario
        self.duracion_minutos = duracion_minutos
        self.intervalo_preguntas = (duracion_minutos * 60) / preguntas_por_usuario  # segundos entre preguntas
        
        # Métricas
        self.metricas = {
            'tiempos_respuesta': [],
            'errores': [],
            'respuestas_exitosas': 0,
            'respuestas_fallidas': 0,
            'inicio_prueba': None,
            'fin_prueba': None,
            'usuarios_stats': {},
            'pool_stats_historia': []
        }
        
        # Lock para thread safety en métricas
        self.metricas_lock = threading.Lock()
        
        # Preguntas de ejemplo para las pruebas
        self.preguntas_ejemplo = [
            "¿Cuáles son las tarjetas disponibles en Colombia?",
            "¿Qué tipos de tarjetas de crédito soporta Kushki?",
            "¿Cuáles son los códigos de respuesta para transacciones?",
            "¿Cómo implementar pagos con tarjeta de débito?",
            "¿Qué documentación hay disponible para integraciones?",
            "¿Cuáles son las comisiones para pagos internacionales?",
            "¿Cómo configurar webhooks en Kushki?",
            "¿Qué métodos de pago están disponibles en Ecuador?",
            "¿Cuál es el proceso para pagos recurrentes?",
            "¿Cómo manejar errores en las transacciones?",
            "¿Qué es el 3DS y cómo implementarlo?",
            "¿Cuáles son los límites de transacción por país?",
            "¿Cómo funciona el sistema de tokens?",
            "¿Qué información necesito para integrar Kushki?",
            "¿Cuáles son los ambientes de prueba disponibles?",
            "¿Cómo implementar pagos móviles?",
            "¿Qué tipos de reportes ofrece Kushki?",
            "¿Cuál es el proceso de certificación?",
            "¿Cómo manejar reembolsos en Kushki?",
            "¿Qué medidas de seguridad implementa Kushki?"
        ]

    def generar_user_id(self, numero_usuario: int) -> str:
        """Generar ID único para cada usuario de prueba"""
        return f"test_user_{numero_usuario:03d}"

    def obtener_pregunta_aleatoria(self) -> str:
        """Obtener una pregunta aleatoria para la prueba"""
        return random.choice(self.preguntas_ejemplo)

    def registrar_metrica(self, user_id: str, tiempo_respuesta: float, exito: bool, error: str = None):
        """Registrar métricas de forma thread-safe"""
        with self.metricas_lock:
            self.metricas['tiempos_respuesta'].append(tiempo_respuesta)
            
            if exito:
                self.metricas['respuestas_exitosas'] += 1
            else:
                self.metricas['respuestas_fallidas'] += 1
                if error:
                    self.metricas['errores'].append({
                        'user_id': user_id,
                        'timestamp': datetime.now().isoformat(),
                        'error': error
                    })
            
            # Estadísticas por usuario
            if user_id not in self.metricas['usuarios_stats']:
                self.metricas['usuarios_stats'][user_id] = {
                    'preguntas_realizadas': 0,
                    'tiempo_total': 0,
                    'errores': 0
                }
            
            self.metricas['usuarios_stats'][user_id]['preguntas_realizadas'] += 1
            self.metricas['usuarios_stats'][user_id]['tiempo_total'] += tiempo_respuesta
            
            if not exito:
                self.metricas['usuarios_stats'][user_id]['errores'] += 1

    def capturar_stats_pool(self):
        """Capturar estadísticas del pool periódicamente"""
        while not hasattr(self, '_detener_stats'):
            try:
                stats = llm_pool.get_stats()
                timestamp = datetime.now().isoformat()
                
                with self.metricas_lock:
                    self.metricas['pool_stats_historia'].append({
                        'timestamp': timestamp,
                        'stats': stats
                    })
                
                time.sleep(5)  # Capturar cada 5 segundos
            except Exception as e:
                logger.error(f"Error capturando stats del pool: {e}")
                time.sleep(5)

    def simular_usuario(self, numero_usuario: int) -> Dict:
        """Simular las acciones de un usuario durante la prueba"""
        user_id = self.generar_user_id(numero_usuario)
        stats_usuario = {
            'user_id': user_id,
            'preguntas_completadas': 0,
            'tiempo_total': 0,
            'errores': 0,
            'inicio': datetime.now()
        }
        
        logger.info(f"🚀 Iniciando simulación para usuario {user_id}")
        
        # Inicializar historial para este usuario
        with chatbot.conversaciones_lock:
            chatbot.conversaciones[user_id] = []
        
        try:
            for pregunta_num in range(self.preguntas_por_usuario):
                inicio_pregunta = time.time()
                
                try:
                    # Obtener pregunta aleatoria
                    pregunta = self.obtener_pregunta_aleatoria()
                    
                    # Obtener historial actual de forma thread-safe
                    with chatbot.conversaciones_lock:
                        historial_actual = chatbot.conversaciones[user_id].copy()
                    
                    logger.info(f"👤 {user_id} - Pregunta {pregunta_num + 1}/10: {pregunta[:50]}...")
                    
                    # Generar respuesta usando el sistema del chatbot
                    respuesta = chatbot.generar_respuesta(pregunta, historial_actual, user_id)
                    
                    tiempo_respuesta = time.time() - inicio_pregunta
                    
                    # Actualizar historial de forma thread-safe
                    with chatbot.conversaciones_lock:
                        chatbot.conversaciones[user_id].extend([
                            {"role": "user", "content": pregunta},
                            {"role": "assistant", "content": respuesta}
                        ])
                        chatbot.conversaciones[user_id] = chatbot.limitar_historial(chatbot.conversaciones[user_id])
                    
                    # Verificar si la respuesta indica error
                    es_error = any(error_phrase in respuesta.lower() for error_phrase in [
                        "temporalmente saturado", "error inesperado", "varios intentos"
                    ])
                    
                    # Registrar métricas
                    self.registrar_metrica(user_id, tiempo_respuesta, not es_error, 
                                         respuesta if es_error else None)
                    
                    stats_usuario['preguntas_completadas'] += 1
                    stats_usuario['tiempo_total'] += tiempo_respuesta
                    
                    if es_error:
                        stats_usuario['errores'] += 1
                    
                    logger.info(f"✅ {user_id} - Respuesta recibida en {tiempo_respuesta:.2f}s")
                    
                except Exception as e:
                    tiempo_respuesta = time.time() - inicio_pregunta
                    error_msg = str(e)
                    
                    logger.error(f"❌ {user_id} - Error en pregunta {pregunta_num + 1}: {error_msg}")
                    
                    self.registrar_metrica(user_id, tiempo_respuesta, False, error_msg)
                    stats_usuario['errores'] += 1
                
                # Esperar antes de la siguiente pregunta (excepto en la última)
                if pregunta_num < self.preguntas_por_usuario - 1:
                    # Agregar variación aleatoria del ±20% al intervalo
                    variacion = random.uniform(0.8, 1.2)
                    tiempo_espera = self.intervalo_preguntas * variacion
                    time.sleep(tiempo_espera)
        
        except Exception as e:
            logger.error(f"💥 Error crítico para usuario {user_id}: {e}")
        
        finally:
            # Limpiar memoria del usuario al finalizar
            chatbot.limpiar_memoria_usuario(user_id)
        
        stats_usuario['fin'] = datetime.now()
        stats_usuario['duracion_total'] = (stats_usuario['fin'] - stats_usuario['inicio']).total_seconds()
        
        logger.info(f"🏁 Usuario {user_id} completado: {stats_usuario['preguntas_completadas']}/10 preguntas, {stats_usuario['errores']} errores")
        
        return stats_usuario

    def ejecutar_prueba(self) -> Dict:
        """Ejecutar la prueba de estrés completa"""
        logger.info("🧪 INICIANDO PRUEBA DE ESTRÉS")
        logger.info(f"📊 Configuración: {self.num_usuarios} usuarios, {self.preguntas_por_usuario} preguntas c/u, {self.duracion_minutos} minutos")
        
        # Verificar que el sistema esté listo
        self._verificar_sistema()
        
        self.metricas['inicio_prueba'] = datetime.now()
        
        # Iniciar captura de estadísticas del pool
        stats_thread = threading.Thread(target=self.capturar_stats_pool, daemon=True)
        stats_thread.start()
        
        # Ejecutar usuarios concurrentes
        resultados_usuarios = []
        
        try:
            with ThreadPoolExecutor(max_workers=self.num_usuarios) as executor:
                # Enviar todos los usuarios de una vez
                futures = {
                    executor.submit(self.simular_usuario, i): i 
                    for i in range(1, self.num_usuarios + 1)
                }
                
                # Recoger resultados conforme van completando
                for future in as_completed(futures):
                    usuario_num = futures[future]
                    try:
                        resultado = future.result()
                        resultados_usuarios.append(resultado)
                        logger.info(f"✅ Usuario {usuario_num} completado")
                    except Exception as e:
                        logger.error(f"❌ Usuario {usuario_num} falló: {e}")
        
        except Exception as e:
            logger.error(f"💥 Error durante la ejecución de la prueba: {e}")
        
        finally:
            # Detener captura de estadísticas
            self._detener_stats = True
        
        self.metricas['fin_prueba'] = datetime.now()
        
        # Generar reporte final
        reporte = self._generar_reporte(resultados_usuarios)
        
        logger.info("🎉 PRUEBA DE ESTRÉS COMPLETADA")
        return reporte

    def _verificar_sistema(self):
        """Verificar que el sistema esté listo para las pruebas"""
        logger.info("🔍 Verificando sistema...")
        
        # Verificar pool de LLMs
        stats = llm_pool.get_stats()
        logger.info(f"📊 Pool LLM: {stats['available']}/{stats['pool_size']} disponibles")
        
        if stats['available'] == 0:
            raise Exception("No hay LLMs disponibles en el pool")
        
        # Verificar que los documentos estén cargados
        if not chatbot.system_prompt:
            logger.warning("⚠️ System prompt vacío, cargando documentos...")
            chatbot.cargar_documentos()
        
        logger.info("✅ Sistema verificado y listo")

    def _generar_reporte(self, resultados_usuarios: List[Dict]) -> Dict:
        """Generar reporte detallado de la prueba"""
        duracion_total = (self.metricas['fin_prueba'] - self.metricas['inicio_prueba']).total_seconds()
        
        reporte = {
            'resumen': {
                'usuarios_simulados': self.num_usuarios,
                'usuarios_completados': len(resultados_usuarios),
                'preguntas_por_usuario': self.preguntas_por_usuario,
                'duracion_total_segundos': duracion_total,
                'duracion_total_minutos': duracion_total / 60,
                'preguntas_totales_esperadas': self.num_usuarios * self.preguntas_por_usuario,
                'preguntas_exitosas': self.metricas['respuestas_exitosas'],
                'preguntas_fallidas': self.metricas['respuestas_fallidas'],
                'tasa_exito': (self.metricas['respuestas_exitosas'] / max(1, self.metricas['respuestas_exitosas'] + self.metricas['respuestas_fallidas'])) * 100
            },
            'rendimiento': {},
            'usuarios': resultados_usuarios,
            'errores': self.metricas['errores'],
            'pool_stats': self.metricas['pool_stats_historia']
        }
        
        # Estadísticas de rendimiento
        if self.metricas['tiempos_respuesta']:
            tiempos = self.metricas['tiempos_respuesta']
            reporte['rendimiento'] = {
                'tiempo_respuesta_promedio': statistics.mean(tiempos),
                'tiempo_respuesta_mediana': statistics.median(tiempos),
                'tiempo_respuesta_min': min(tiempos),
                'tiempo_respuesta_max': max(tiempos),
                'tiempo_respuesta_std': statistics.stdev(tiempos) if len(tiempos) > 1 else 0,
                'throughput_preguntas_por_segundo': len(tiempos) / duracion_total if duracion_total > 0 else 0
            }
        
        return reporte

def imprimir_reporte(reporte: Dict):
    """Imprimir reporte de manera legible"""
    print("\n" + "="*60)
    print("📊 REPORTE DE PRUEBA DE ESTRÉS")
    print("="*60)
    
    resumen = reporte['resumen']
    print(f"\n📈 RESUMEN:")
    print(f"   👥 Usuarios simulados: {resumen['usuarios_simulados']}")
    print(f"   ✅ Usuarios completados: {resumen['usuarios_completados']}")
    print(f"   ⏱️  Duración total: {resumen['duracion_total_minutos']:.1f} minutos")
    print(f"   📝 Preguntas esperadas: {resumen['preguntas_totales_esperadas']}")
    print(f"   ✅ Preguntas exitosas: {resumen['preguntas_exitosas']}")
    print(f"   ❌ Preguntas fallidas: {resumen['preguntas_fallidas']}")
    print(f"   📊 Tasa de éxito: {resumen['tasa_exito']:.1f}%")
    
    if 'rendimiento' in reporte and reporte['rendimiento']:
        perf = reporte['rendimiento']
        print(f"\n⚡ RENDIMIENTO:")
        print(f"   📊 Tiempo promedio: {perf['tiempo_respuesta_promedio']:.2f}s")
        print(f"   📊 Tiempo mediana: {perf['tiempo_respuesta_mediana']:.2f}s")
        print(f"   📊 Tiempo mínimo: {perf['tiempo_respuesta_min']:.2f}s")
        print(f"   📊 Tiempo máximo: {perf['tiempo_respuesta_max']:.2f}s")
        print(f"   📊 Desviación estándar: {perf['tiempo_respuesta_std']:.2f}s")
        print(f"   🚀 Throughput: {perf['throughput_preguntas_por_segundo']:.2f} preguntas/segundo")
    
    if reporte['errores']:
        print(f"\n❌ ERRORES ({len(reporte['errores'])}):")
        for error in reporte['errores'][:5]:  # Mostrar solo los primeros 5
            print(f"   • {error['user_id']}: {error['error'][:100]}...")
        if len(reporte['errores']) > 5:
            print(f"   ... y {len(reporte['errores']) - 5} errores más")
    
    print("\n" + "="*60)

def main():
    """Función principal para ejecutar las pruebas"""
    print("🧪 INICIANDO PRUEBAS DE ESTRÉS PARA CHASKI BOT")
    print("="*50)
    
    try:
        # Configuración de la prueba
        tester = EstressTester(
            num_usuarios=18,
            preguntas_por_usuario=10,
            duracion_minutos=10
        )
        
        # Ejecutar prueba
        reporte = tester.ejecutar_prueba()
        
        # Mostrar resultados
        imprimir_reporte(reporte)
        
        # Guardar reporte en archivo
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"reporte_estres_{timestamp}.txt"
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write("REPORTE DE PRUEBA DE ESTRÉS - CHASKI BOT\n")
            f.write("="*50 + "\n\n")
            f.write(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            import json
            f.write(json.dumps(reporte, indent=2, ensure_ascii=False, default=str))
        
        print(f"\n💾 Reporte detallado guardado en: {filename}")
        
    except KeyboardInterrupt:
        print("\n🛑 Prueba interrumpida por el usuario")
    except Exception as e:
        print(f"\n💥 Error durante las pruebas: {e}")
        logger.error(f"Error en pruebas de estrés: {e}")

if __name__ == "__main__":
    main() 