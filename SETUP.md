# Medtorek: instrucciones

Automatización de contenidos científicos para X, LinkedIn e Instagram usando Buffer API y GitHub Actions.

**Desactivada por defecto**: AUTO_PUBLISH=false. Ningún mensaje se publicará hasta que se configure y active expresamente.

1. REVOCAR la clave de Buffer que apareció en el chat. Crear otra y guardarla en GitHub → Settings → Secrets and variables → Actions → Secrets como BUFFER_API_KEY.
2. Guardar la clave del nivel gratuito de Google AI Studio como GEMINI_API_KEY. En Variables, elegir GEMINI_MODEL cuando se confirme un modelo de cuota gratuita.
3. Comprobar que Buffer tiene exactamente una cuenta X, una LinkedIn y una Instagram profesional apta para publicación automática.
4. Ejecutar manualmente el workflow con preview_only=true. Revisar los archivos públicos generados en public/ y el estado en state/.
5. Configurar en Buffer sus horarios de publicación. Solo cuando haya pasado una prueba de extremo a extremo, activar en GitHub Actions Variables AUTO_PUBLISH=true.

ATENCIÓN: repositorio público; nunca incluir credenciales, nombres de pacientes ni datos clínicos. Buffer necesita imágenes disponibles en URLs públicas. Se verifican metadatos DOI con Crossref y el resumen indexado por PubMed, NO el texto completo de un manuscrito: la revisión crítica automática NO reemplaza una lectura científica humana.

GitHub schedule puede sufrir retrasos; ejecución diaria pretendida ~08:30 Europe/Copenhagen. El envío a Buffer añade contenidos a sus colas; la hora final depende de su calendario.

Documentación: https://developers.buffer.com/guides/your-first-post.html
