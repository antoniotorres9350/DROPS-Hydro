# DROPS-Hydro MX 0.5.3

## Uso

1. Instala `DROPS-Hydro_MX_v0.5.3.zip` desde el administrador de complementos y reinicia QGIS.
2. En CHIRPS, **Carpeta de descarga CHIRPS → Elegir carpeta** aparece ahora al inicio de la pestaña. La ruta se conserva en la sesión. Allí se guardan la lluvia diaria CSV, la caché de ClimateSERV y los ajustes estadísticos. Se comprueba que la carpeta permita escritura antes de enviar la solicitud. El modo experimental CHIRPS v3 también exporta su CSV a esa carpeta.
3. En Gastos / Tc, deja activado **Usar automáticamente los archivos recortados de la cuenca**. El filled DEM se selecciona automáticamente y se muestran las rutas de MDE, pendiente, acumulación, dirección, escurrimientos y red recortados disponibles. Se actualizan después de delimitar y al entrar a la pestaña. Si todavía no hay recortes, el cálculo de terreno los genera a partir de los insumos existentes. Desactivar la opción permite elegir manualmente otro filled DEM.
4. Para CN automático, deja activado WorldCover remoto y **Suelo local (opcional)** vacío. Pulsa **Calcular CN medio de la cuenca**, o directamente el cálculo automático de gastos, que obtiene primero los parámetros necesarios. No necesitas proporcionar un archivo de grupo hidrológico.

## CN sin insumos manuales de suelo

Se cruza WorldCover 2021 v200 con HYSOGs250m, obtenido automáticamente por ventanas desde el GeoTIFF optimizado de Natural Capital Alliance. Su reclasificación documentada representa los grupos A/B/C/D como 1/2/3/4 y asigna los grupos duales a D, bajo el supuesto de suelo no drenado. El CN se estima por combinación de cobertura y grupo de suelo con la tabla AMC-II del plugin, y se promedia por superficie sobre la cuenca. No se deduce el tipo de suelo solo del MDE ni se asigna arbitrariamente un mismo grupo a toda la cuenca.

Se guardan `cn_cuenca.tif`, `grupo_hidrologico_cuenca.tif`, `cobertura_cuenca.tif`, `cn_areas.csv`, `cn_crosswalk.csv` y `fuentes_CN.json` dentro de una subcarpeta CN de la carpeta de hidrología. Una caché permite reutilizar los recortes para la misma cuenca, fuentes y resolución; si estos cambian, se obtienen nuevos datos. El suelo local sigue disponible como alternativa opcional.

HYSOGs tiene resolución aproximada de **250 m**. Calcular una cuadrícula de 30 m no añade detalle real al suelo. El resultado es una estimación bajo los supuestos de la tabla de CN, no una medición directa. Se mantiene el requisito de al menos 95 % de superficie con CN válido; una descarga fallida o datos insuficientes generan un error, no un CN inventado. Se requiere internet para obtener nuevos recortes.

Las derivadas de terreno y SIMWE conservan celdas vecinas del filled DEM original como apoyo en el borde. Sus estadísticas y productos se restringen al polígono de cuenca; esta medida evita interpretar NoData como un desnivel.

## Descargas y certificados

En Windows, cuando no hay un certificado CA configurado explícitamente, la lectura remota de CN utiliza los certificados públicos de confianza del sistema en una configuración temporal del trabajador GDAL. Esto resuelve incompatibilidades con la inspección HTTPS del antivirus observadas en este equipo. Se mantiene la validación TLS y se restaura la configuración al finalizar; no se modifica el almacén de confianza de Windows.

## Verificación

Probado con QGIS 3.44.14:

- Descarga real de ventanas de WorldCover y HYSOGs para una cuenca sintética situada en México; 100 % de cobertura válida en ese caso.
- Interfaz completa sin archivo local de suelo: selección automática del filled DEM recortado y red, pendiente conocida del 10 %, CN remoto, tiempos de concentración, 45 estimaciones de gastos y CSV.
- Reutilización de suelo y cobertura desde caché con la lectura remota deshabilitada en la prueba.
- Guardado del CSV diario mediante el callback de CHIRPS y exportación de ajustes a una carpeta elegida con espacios en su nombre. Esto comprueba la ruta de guardado; no constituye una nueva descarga completa desde ClimateSERV.
- Pruebas numéricas de distribuciones, Strahler, promedio de CN y conservación del volumen de hidrogramas.

## Fuentes

- [HYSOGs250m — Natural Capital Alliance, Stanford University](https://data.naturalcapitalalliance.stanford.edu/dataset/sts-c248985b3821c0f91f458b3188b4ab1b92b3e95679ec6bd99565bab55fd8616e), COG reclasificado, licencia CC BY 4.0.
- Ross, C.W. et al. (2018). [Global Hydrologic Soil Groups, ORNL DAAC](https://doi.org/10.3334/ORNLDAAC/1566).
- [ESA WorldCover: acceso a datos](https://esa-worldcover.org/en/data-access).

Se mantienen las funciones y límites de las versiones anteriores documentados dentro del ZIP.
