from qgis.PyQt.QtWidgets import (QAction,QDialog,QVBoxLayout,QFormLayout,QComboBox,QDoubleSpinBox,QSpinBox,
 QPushButton,QTextEdit,QLabel,QMessageBox,QGroupBox,QTabWidget,QWidget,QHBoxLayout,QLineEdit,QFileDialog,QProgressBar,QCheckBox,QScrollArea,QApplication)
from qgis.PyQt.QtCore import Qt, QElapsedTimer, QTimer, QSettings
from qgis.core import (QgsProject,QgsRasterLayer,QgsVectorLayer,QgsCoordinateReferenceSystem,QgsCoordinateTransform,
 QgsPointXY,QgsRasterBandStats,QgsApplication,QgsProcessingFeedback,QgsWkbTypes,QgsProcessingContext,QgsProcessingAlgRunnerTask,
 QgsSpatialIndex,QgsGeometry,QgsFeature,QgsTask,QgsSnappingConfig,QgsFeatureRequest)
import math, os, csv, datetime, statistics, tempfile, sys, io, json, time, urllib.request, urllib.parse, threading, glob
from qgis.gui import QgsMapTool, QgsMapToolEmitPoint


class DHOutletMapTool(QgsMapTool):
    """Map tool dedicated to selecting a basin outlet.

    Uses the QGIS map mouse event (and therefore QGIS native snapping when
    enabled) instead of relying only on a generic canvasClicked signal.
    """
    def __init__(self, canvas, dialog):
        super().__init__(canvas)
        self.dialog = dialog
        try:
            self.setCursor(Qt.CrossCursor)
        except Exception:
            pass

    def canvasReleaseEvent(self, event):
        try:
            map_point = event.mapPoint()
            snapped = None
            try:
                snapped = event.snapPoint()
            except Exception:
                snapped = None
            self.dialog._outlet_clicked_from_map(map_point, snapped)
        except Exception as e:
            QMessageBox.warning(self.dialog, 'Selección de outlet', str(e))



from .chirps_task import ClimateServChirpsTask
from .terrain_task import TerrainTask
from .advanced import AdvancedHydrology

class DHProgressFeedback(QgsProcessingFeedback):
    """Processing feedback that never touches Qt widgets from worker threads.

    QGIS/GRASS/GDAL may call feedback methods from a QgsTask worker thread.
    Updating widgets or pumping the Qt event loop from those callbacks can crash
    QGIS on Windows (access violation).  We only store the latest state here; a
    QTimer owned by the dialog polls it safely from the GUI thread.
    """
    def __init__(self):
        super().__init__()
        self._lock=threading.Lock()
        self._latest_progress=None
        self._latest_message=''
        self._message_serial=0
    def _store_message(self, text):
        with self._lock:
            self._latest_message=str(text)
            self._message_serial += 1
    def setProgress(self, progress):
        super().setProgress(progress)
        with self._lock:
            try: self._latest_progress=float(progress)
            except Exception: self._latest_progress=None
    def pushInfo(self, info):
        super().pushInfo(info); self._store_message(info)
    def pushCommandInfo(self, info):
        super().pushCommandInfo(info); self._store_message(info)
    def pushConsoleInfo(self, info):
        super().pushConsoleInfo(info); self._store_message(info)
    def reportError(self, error, fatalError=False):
        super().reportError(error, fatalError); self._store_message('ERROR: '+str(error))
    def snapshot(self):
        with self._lock:
            return self._latest_progress, self._latest_message, self._message_serial

class DROPSHydroMX:
    def __init__(self, iface):
        self.iface=iface; self.action=None; self.dialog=None
    def initGui(self):
        self.action=QAction('DROPS-Hydro MX',self.iface.mainWindow()); self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu('&DROPS-Hydro MX',self.action); self.iface.addToolBarIcon(self.action)
    def unload(self):
        if self.dialog is not None:
            try:
                self.dialog.cancel_background_task()
                for name in ('_chirps_task','_terrain_task','_extra_task'):
                    task=getattr(self.dialog,name,None)
                    if task is not None: task.callback=None
                self.dialog._save_session_state()
                self.dialog.close()
            except Exception:
                pass
            self.dialog=None
        if self.action:
            self.iface.removePluginMenu('&DROPS-Hydro MX',self.action); self.iface.removeToolBarIcon(self.action)
    def run(self):
        # Non-modal persistent dialog: the QGIS map canvas must remain interactive
        # while the user picks an outlet. Keeping the same dialog instance also
        # preserves the current workflow when the window is closed/reopened.
        if self.dialog is None:
            self.dialog=DROPSHydroDialog(self.iface)
        else:
            try: self.dialog.recover_existing_results(silent=True)
            except Exception: pass
        self.dialog.setModal(False)
        self.dialog.show(); self.dialog.raise_(); self.dialog.activateWindow()

class DROPSHydroDialog(AdvancedHydrology,QDialog):
    def __init__(self,iface):
        super().__init__(iface.mainWindow()); self.iface=iface; self.setWindowTitle('DROPS-Hydro MX v0.5.4'); self.resize(820,780)
        self.chirps_series=[]; self.chirps_daily=[]; self.chirps_corrected_daily=[]; self.design={}; self.outlet_map=None; self.outlet_stream=None; self.outlet_crs=None; self.auto={}; self.maptool=None; self.stream_index=None; self.outlet_layer_id=None; self._old_snapping_config=None
        L=QVBoxLayout(self); L.addWidget(QLabel('DROPS-Hydro MX — análisis de cuencas, extremos hidrológicos, inundaciones, sequías e IA/ML.')); L.addWidget(QLabel('Prof. Juan Antonio Torres Martinez — Tecnologico de Monterrey\nData-driven Research On aquatic Pollution and Sustainability (DROPS Research Group)\nantoniotorres@tec.mx | antoniotorres9350@gmail.com'))
        self.tabs=QTabWidget(); L.addWidget(self.tabs,1)
        self._tab_auto(); self._tab_inputs(); self._tab_morph(); self._tab_rain(); self._tab_hydro(); self._tab_flood(); self._tab_drought(); self._tab_ml()
        # Each page scrolls independently; tab labels and progress remain visible.
        for i in range(self.tabs.count()):
            page=self.tabs.widget(i); title=self.tabs.tabText(i)
            self.tabs.removeTab(i)
            scroll=QScrollArea(self.tabs); scroll.setWidgetResizable(True)
            scroll.setWidget(page); self.tabs.insertTab(i,scroll,title)
        self.tabs.setCurrentIndex(0)
        for label in self.findChildren(QLabel):
            label.setWordWrap(True)
        screen=QApplication.screenAt(self.iface.mainWindow().frameGeometry().center()) or QApplication.primaryScreen()
        if screen:
            available=screen.availableGeometry()
            self.resize(min(820,int(available.width()*0.9)),min(780,int(available.height()*0.9)))
        self.progress=QProgressBar(); self.progress.setRange(0,100); self.progress.setValue(0); self.progress.setFormat('Listo — %p%')
        self.progress_label=QLabel('Sin proceso activo.'); self.progress_label.setWordWrap(True)
        L.addWidget(self.progress); L.addWidget(self.progress_label)
        self.out=QTextEdit(); self.out.setReadOnly(True); self.out.setMinimumHeight(80); self.out.setMaximumHeight(190); L.addWidget(self.out)
        self.cancel_task_button=QPushButton('Cancelar descarga / pendientes')
        self.cancel_task_button.setEnabled(False)
        self.cancel_task_button.clicked.connect(self.cancel_background_task)
        L.addWidget(self.cancel_task_button)
        b=QPushButton('Cerrar'); b.clicked.connect(self._close_dialog); L.addWidget(b)
        self._current_feedback=None
        self._feedback_serial_seen=-1
        self._progress_timer=QTimer(self)
        self._progress_timer.setInterval(250)
        self._progress_timer.timeout.connect(self._poll_processing_feedback)
        # Restore working directory and any reusable outputs after the UI exists.
        QTimer.singleShot(0, lambda: self.recover_existing_results(silent=True))

    def cancel_background_task(self):
        for name in ('_chirps_task','_terrain_task','_extra_task'):
            task=getattr(self,name,None)
            if task is not None: task.cancel()
        self.progress_label.setText('Cancelando; una petición HTTP activa puede tardar hasta agotar su tiempo de espera.')

    def combo_layers(self,kind,none=False):
        c=QComboBox();
        if none: c.addItem('(ninguno)',None)
        for x in QgsProject.instance().mapLayers().values():
            if kind=='raster' and isinstance(x,QgsRasterLayer): c.addItem(x.name(),x.id())
            if kind=='poly' and isinstance(x,QgsVectorLayer) and x.geometryType()==2: c.addItem(x.name(),x.id())
            if kind=='line' and isinstance(x,QgsVectorLayer) and x.geometryType()==1: c.addItem(x.name(),x.id())
        return c
    def layer(self,c):
        i=c.currentData(); return QgsProject.instance().mapLayer(i) if i else None
    def _tab_auto(self):
        w=QWidget(); f=QFormLayout(w)
        self.dem_files=[]
        self.demfiles_label=QLabel('Ningún archivo seleccionado'); self.demfiles_label.setWordWrap(True)
        bdem=QPushButton('1. Seleccionar uno o varios MDE'); bdem.clicked.connect(self.pick_dem_files)
        self.active_dem_label=QLabel('Sin MDE activo'); self.active_dem_label.setWordWrap(True)
        bmerge=QPushButton('2. Unir MDE (OPCIONAL)'); bmerge.clicked.connect(self.merge_dems)
        bsingle=QPushButton('Usar MDE seleccionado sin unir'); bsingle.clicked.connect(self.use_single_dem)
        self.threshold=QSpinBox(); self.threshold.setRange(10,10000000); self.threshold.setValue(10000)
        self.flow_mode=QComboBox(); self.flow_mode.addItems(['Rápido — D8/SFD (recomendado para red y outlet)','Normal — MFD','MDE grande — D8 + disk swap'])
        self.grass_memory=QSpinBox(); self.grass_memory.setRange(128,32768); self.grass_memory.setValue(2048); self.grass_memory.setSuffix(' MB')
        self.analysis_res=QComboBox(); self.analysis_res.addItems(['Auto rápido (máx. ~25 M celdas)','Original','10 m','15 m','30 m','60 m'])
        self.analysis_info=QLabel('La resolución rápida se calcula al iniciar el paso 3. El MDE original se conserva.'); self.analysis_info.setWordWrap(True)
        self.fill_sinks=QCheckBox('Acondicionar MDE con r.fill.dir antes de calcular flujo (recomendado)'); self.fill_sinks.setChecked(True)
        self.snapcells=QSpinBox(); self.snapcells.setRange(1,100); self.snapcells.setValue(10); self.snapcells.setSuffix(' celdas')
        self.autodir=QLineEdit(os.path.join(tempfile.gettempdir(),'DROPSHydroMX_auto'))
        bo=QPushButton('Carpeta'); bo.clicked.connect(self.pickautodir); h=QHBoxLayout(); h.addWidget(self.autodir); h.addWidget(bo)
        bflow=QPushButton('3. Calcular dirección + acumulación'); bflow.clicked.connect(self.generate_flow)
        bnet=QPushButton('4. Generar / regenerar red de drenaje'); bnet.clicked.connect(self.generate_network)
        self.network_status=QLabel('Esperando MDE'); self.network_status.setWordWrap(True)
        self.outletlabel=QLabel('No seleccionado')
        bp=QPushButton('5. Seleccionar punto de salida SOBRE la red'); bp.clicked.connect(self.pick_outlet)
        delineate=QPushButton('6. Delimitar cuenca'); delineate.clicked.connect(self.delineate_from_prepared)
        br=QPushButton('Actualizar listas de capas'); br.clicked.connect(self.refresh_layers)
        f.addRow(bdem); f.addRow('MDE seleccionados:',self.demfiles_label); f.addRow(bmerge); f.addRow(bsingle)
        f.addRow('MDE activo:',self.active_dem_label); f.addRow('Carpeta de trabajo:',h)
        f.addRow('Resolución para red/outlet:',self.analysis_res); f.addRow('',self.analysis_info); f.addRow(self.fill_sinks)
        f.addRow('Modo de flujo:',self.flow_mode); f.addRow('Memoria GRASS:',self.grass_memory); f.addRow(bflow); f.addRow('Umbral de red (celdas):',self.threshold); f.addRow(bnet)
        f.addRow('Estado:',self.network_status); f.addRow('Tolerancia snap del outlet:',self.snapcells); f.addRow('Punto de salida:',self.outletlabel); f.addRow(bp); f.addRow(delineate); f.addRow(br)
        f.addRow(QLabel('Modo rápido: la red puede calcularse en un MDE temporal remuestreado. Después se conserva el MDE original para análisis posteriores.'))

        reuse=QGroupBox('Reanudar / reutilizar resultados existentes')
        rf=QFormLayout(reuse)
        self.reuse_dem=self.combo_layers('raster',True)
        self.reuse_acc=self.combo_layers('raster',True)
        self.reuse_dir=self.combo_layers('raster',True)
        self.reuse_stream=self.combo_layers('line',True)
        self.reuse_basin=self.combo_layers('poly',True)
        buse=QPushButton('Usar capas seleccionadas y continuar'); buse.clicked.connect(self.use_existing_layers)
        brecover=QPushButton('Recuperar automáticamente desde proyecto/carpeta'); brecover.clicked.connect(self.recover_existing_results)
        rf.addRow('MDE existente:',self.reuse_dem); rf.addRow('Acumulación existente:',self.reuse_acc); rf.addRow('Dirección existente:',self.reuse_dir)
        rf.addRow('Red lineal existente:',self.reuse_stream); rf.addRow('Cuenca existente:',self.reuse_basin); rf.addRow(buse); rf.addRow(brecover)
        f.addRow(reuse)
        self.tabs.addTab(w,'1. MDE → red → outlet → cuenca')
    def pick_dem_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,'Seleccionar MDE','','MDE compatibles (*.tif *.tiff *.img *.bil *.BIL *.dem *.asc);;GeoTIFF (*.tif *.tiff);;ESRI BIL (*.bil *.BIL);;ERDAS IMG (*.img);;ASCII Grid (*.asc);;Todos (*.*)')
        if paths:
            self.dem_files=paths
            names=[os.path.basename(x) for x in paths]
            self.demfiles_label.setText(f'{len(paths)} MDE: '+', '.join(names[:5])+(' ...' if len(names)>5 else ''))

    def pickautodir(self):
        p=QFileDialog.getExistingDirectory(self,'Carpeta de trabajo')
        if p:
            self.autodir.setText(p); QSettings().setValue('DROPSHydroMX/workdir',p)

    def _activate_stream_snapping(self, stream):
        """Temporarily enable QGIS native vertex/segment snapping.

        ArcGeek uses the canvas snapping configuration before asking for the
        pour point.  We do the same, while still validating/snap-projecting the
        final click against the selected drainage layer.
        """
        utils=self.iface.mapCanvas().snappingUtils()
        try:
            self._old_snapping_config=utils.config()
        except Exception:
            self._old_snapping_config=None
        try:
            cfg=utils.config()
            cfg.setEnabled(True)
            try:
                cfg.setTypeFlag(QgsSnappingConfig.VertexFlag | QgsSnappingConfig.SegmentFlag)
            except (TypeError, AttributeError):
                try: cfg.setType(QgsSnappingConfig.Vertex | QgsSnappingConfig.Segment)
                except Exception: pass
            # Prefer all visible layers for native snapping; the final point is
            # nevertheless projected to the drainage network below, so snapping
            # to another visible layer cannot create an invalid outlet.
            try: cfg.setMode(QgsSnappingConfig.AllLayers)
            except Exception: pass
            utils.setConfig(cfg)
        except Exception:
            pass
        try: self.iface.setActiveLayer(stream)
        except Exception: pass

    def _restore_snapping(self):
        if self._old_snapping_config is not None:
            try: self.iface.mapCanvas().snappingUtils().setConfig(self._old_snapping_config)
            except Exception: pass
        self._old_snapping_config=None

    def pick_outlet(self):
        stream=self._stream_layer()
        if stream is None:
            QMessageBox.warning(self,'DROPS-Hydro MX','Primero genere o cargue una red de drenaje lineal (paso 4 / Reanudar).')
            return
        self._build_stream_index(stream)
        try:
            if self.maptool is not None:
                self.iface.mapCanvas().unsetMapTool(self.maptool)
        except Exception:
            pass
        self._activate_stream_snapping(stream)
        # Dedicated map tool.  QGIS sends a QgsMapMouseEvent here, so native
        # snapping is available via event.snapPoint().
        self.maptool=DHOutletMapTool(self.iface.mapCanvas(),self)
        self.iface.mapCanvas().setMapTool(self.maptool)
        self.iface.mapCanvas().setFocus()
        self.outletlabel.setText('Modo selección activo: haga clic sobre/cerca de una línea…')
        self.network_status.setText('Selección de outlet activa. Snapping QGIS a vértices/segmentos habilitado temporalmente.')
        self.iface.messageBar().pushMessage('DROPS-Hydro MX','SELECCIÓN ACTIVA: haga clic sobre o cerca de una LÍNEA de drenaje.',level=0,duration=15)

    def _stream_layer(self):
        sid=self.auto.get('streams_id')
        lyr=QgsProject.instance().mapLayer(sid) if sid else None
        if isinstance(lyr,QgsVectorLayer) and lyr.geometryType()==1:
            return lyr
        return None

    def _build_stream_index(self, stream):
        try:
            self.stream_index=QgsSpatialIndex(stream.getFeatures(),flags=QgsSpatialIndex.FlagStoreFeatureGeometries)
        except Exception:
            self.stream_index=QgsSpatialIndex(stream.getFeatures())

    def _show_single_outlet(self, snapped, crs):
        # One outlet feature only; no candidate-point cloud is created.
        if self.outlet_layer_id:
            try: QgsProject.instance().removeMapLayer(self.outlet_layer_id)
            except Exception: pass
            self.outlet_layer_id=None
        vl=QgsVectorLayer(f'Point?crs={crs.authid()}','DH_Outlet','memory')
        pr=vl.dataProvider(); ft=QgsFeature(); ft.setGeometry(QgsGeometry.fromPointXY(snapped)); pr.addFeature(ft); vl.updateExtents()
        QgsProject.instance().addMapLayer(vl); self.outlet_layer_id=vl.id()

    def _outlet_clicked_from_map(self, pt, snapped_hint=None):
        try:
            stream=self._stream_layer()
            if stream is None: raise ValueError('La red lineal ya no está disponible.')
            mapcrs=self.iface.mapCanvas().mapSettings().destinationCrs()
            tr=QgsCoordinateTransform(mapcrs,stream.crs(),QgsProject.instance())
            click_stream=tr.transform(QgsPointXY(pt))
            # Native QGIS snapping gives us a better initial location when the
            # cursor is close to a visible segment.  Regardless of what layer
            # QGIS snapped to, project the result onto the drainage network.
            pstream=click_stream
            if snapped_hint is not None:
                try:
                    hint=QgsPointXY(snapped_hint)
                    pstream=tr.transform(hint)
                except Exception:
                    pstream=click_stream
            if self.stream_index is None: self._build_stream_index(stream)
            ids=self.stream_index.nearestNeighbor(pstream,12) if self.stream_index else []
            if not ids:
                # Retry with the raw click in case the native snapping hint came
                # from another visible layer.
                ids=self.stream_index.nearestNeighbor(click_stream,12) if self.stream_index else []
                pstream=click_stream
            if not ids: raise ValueError('No se encontró una línea de drenaje cercana.')
            best=None; bestd=float('inf')
            for fid in ids:
                ft=stream.getFeature(fid); g=ft.geometry()
                if g is None or g.isEmpty(): continue
                try:
                    d2,cp,after,left=g.closestSegmentWithContext(pstream)
                    d=math.sqrt(max(0.0,float(d2)))
                    if d<bestd: bestd=d; best=QgsPointXY(cp)
                except Exception:
                    ng=g.nearestPoint(QgsGeometry.fromPointXY(pstream))
                    if ng and not ng.isEmpty():
                        cp=ng.asPoint(); d=math.hypot(cp.x()-pstream.x(),cp.y()-pstream.y())
                        if d<bestd: bestd=d; best=QgsPointXY(cp)
            if best is None: raise ValueError('No fue posible ajustar el punto a la red de drenaje.')
            dem=self._flow_dem()
            cell=max(abs(dem.rasterUnitsPerPixelX()),abs(dem.rasterUnitsPerPixelY())) if isinstance(dem,QgsRasterLayer) else 1.0
            tol=max(cell*self.snapcells.value(),cell)
            if bestd>tol:
                raise ValueError(f'El clic está a {bestd:.1f} unidades de la red, fuera de la tolerancia ({tol:.1f}). Acérquese a una línea o aumente la tolerancia de snap.')
            self.outlet_stream=best; self.outlet_crs=stream.crs()
            back=QgsCoordinateTransform(stream.crs(),mapcrs,QgsProject.instance()); self.outlet_map=back.transform(best)
            self.outletlabel.setText(f'{best.x():.3f}, {best.y():.3f} ({stream.crs().authid()}) — snap {bestd:.2f}')
            self._show_single_outlet(best,stream.crs())
            self._save_session_state()
            self.iface.messageBar().pushMessage('DROPS-Hydro MX',f'Outlet ajustado a la red (distancia final: {bestd:.2f}).',level=3,duration=6)
        except Exception as e:
            QMessageBox.warning(self,'Selección de outlet',str(e))
        finally:
            try:
                if self.maptool is not None: self.iface.mapCanvas().unsetMapTool(self.maptool)
            except Exception:
                pass
            self.maptool=None
            self._restore_snapping()
            self.show(); self.raise_(); self.activateWindow()

    # Backward-compatible slot for projects/scripts that referenced the old handler.
    def _outlet_clicked(self, pt, button=None):
        self._outlet_clicked_from_map(pt, None)

    def refresh_layers(self):
        def refill(c,kind,none=False,preferred=None):
            old=preferred or c.currentData(); c.clear()
            if none: c.addItem('(ninguno)',None)
            for x in QgsProject.instance().mapLayers().values():
                ok=(kind=='raster' and isinstance(x,QgsRasterLayer)) or (kind=='poly' and isinstance(x,QgsVectorLayer) and x.geometryType()==2) or (kind=='line' and isinstance(x,QgsVectorLayer) and x.geometryType()==1)
                if ok: c.addItem(x.name(),x.id())
            j=c.findData(old)
            if j>=0: c.setCurrentIndex(j)
        refill(self.basin,'poly',False,self.auto.get('basin_id')); refill(self.dem,'raster',True,self.auto.get('basin_dem_id') or self.auto.get('dem_id')); refill(self.streams,'line',True,self.auto.get('basin_streams_id') or self.auto.get('streams_id'))
        if hasattr(self,'reuse_dem'):
            refill(self.reuse_dem,'raster',True,self.auto.get('dem_id'))
            refill(self.reuse_acc,'raster',True,self._layer_id_for_path(self.auto.get('acc')))
            refill(self.reuse_dir,'raster',True,self._layer_id_for_path(self.auto.get('drainage')))
            refill(self.reuse_stream,'line',True,self.auto.get('streams_id'))
            refill(self.reuse_basin,'poly',True,self.auto.get('basin_id'))

    def _close_dialog(self):
        self._save_session_state()
        try:
            if self.maptool is not None: self.iface.mapCanvas().unsetMapTool(self.maptool)
        except Exception: pass
        self.maptool=None; self._restore_snapping()
        self.hide()

    def closeEvent(self, event):
        try: self._save_session_state()
        except Exception: pass
        try:
            if self.maptool is not None: self.iface.mapCanvas().unsetMapTool(self.maptool)
        except Exception: pass
        self.maptool=None; self._restore_snapping()
        super().closeEvent(event)

    @staticmethod
    def _source_path(layer):
        if layer is None: return ''
        try: return layer.source().split('|')[0]
        except Exception: return ''

    def _layer_id_for_path(self, path):
        if not path: return None
        try: target=os.path.normcase(os.path.abspath(path))
        except Exception: return None
        for lyr in QgsProject.instance().mapLayers().values():
            try:
                src=os.path.normcase(os.path.abspath(self._source_path(lyr)))
                if src==target: return lyr.id()
            except Exception: pass
        return None

    def _find_named_layer(self, names, kind=None):
        names=[n.lower() for n in names]
        for lyr in QgsProject.instance().mapLayers().values():
            try:
                if lyr.name().lower() not in names: continue
                if kind=='raster' and not isinstance(lyr,QgsRasterLayer): continue
                if kind=='line' and not (isinstance(lyr,QgsVectorLayer) and lyr.geometryType()==1): continue
                if kind=='poly' and not (isinstance(lyr,QgsVectorLayer) and lyr.geometryType()==2): continue
                return lyr
            except Exception: pass
        return None

    def _load_raster_once(self, path, name):
        if not path or not os.path.exists(path): return None
        lid=self._layer_id_for_path(path)
        if lid:
            lyr=QgsProject.instance().mapLayer(lid)
            if isinstance(lyr,QgsRasterLayer) and lyr.isValid(): return lyr
        lyr=QgsRasterLayer(path,name)
        if lyr.isValid(): QgsProject.instance().addMapLayer(lyr); return lyr
        return None

    def _load_vector_once(self, path, name, expected_geom=None):
        if not path or not os.path.exists(path): return None
        lid=self._layer_id_for_path(path)
        if lid:
            lyr=QgsProject.instance().mapLayer(lid)
            if isinstance(lyr,QgsVectorLayer) and lyr.isValid():
                if expected_geom is None or lyr.geometryType()==expected_geom: return lyr
        lyr=QgsVectorLayer(path,name,'ogr')
        if lyr.isValid() and (expected_geom is None or lyr.geometryType()==expected_geom):
            QgsProject.instance().addMapLayer(lyr); return lyr
        return None

    def _save_session_state(self):
        try:
            prj=QgsProject.instance(); group='DROPSHydroMX'
            vals={
                'workdir': self.autodir.text().strip() if hasattr(self,'autodir') else '',
                'dem_path': self.auto.get('dem_path',''),
                'analysis_dem_path': self.auto.get('analysis_dem_path',''),
                'filled_dem_path': self.auto.get('filled_dem_path',''),
                'acc': self.auto.get('acc',''),
                'drainage': self.auto.get('drainage',''),
                'stream_raster': self.auto.get('stream_raster',''),
                'basin_streams_path': self.auto.get('basin_streams_path',''),
                'basin_rasters': json.dumps(self.auto.get('basin_rasters',{})),
                'basin_products_key': self.auto.get('basin_products_key',''),
                'streams_path': self.auto.get('streams_path',''),
                'basin_path': self.auto.get('basin_path',''),
                'outlet_x': str(self.outlet_stream.x()) if self.outlet_stream is not None else str(self.auto.get('outlet_x','')),
                'outlet_y': str(self.outlet_stream.y()) if self.outlet_stream is not None else str(self.auto.get('outlet_y','')),
                'outlet_crs': self.outlet_crs.authid() if self.outlet_crs is not None else str(self.auto.get('outlet_crs','')),
                'threshold': str(self.threshold.value()) if hasattr(self,'threshold') else ''
            }
            for name in ('chirps_output','hydro_output','soil_input','cover_input','filled_input','cn_table_input','manning_raster','manning_table'):
                if hasattr(self,name): vals[name]=getattr(self,name).text()
            if hasattr(self,'worldcover'): vals['worldcover']='1' if self.worldcover.isChecked() else '0'
            if hasattr(self,'use_basin_inputs'): vals['use_basin_inputs']='1' if self.use_basin_inputs.isChecked() else '0'
            if hasattr(self,'manning_mode'): vals['manning_mode']=str(self.manning_mode.currentIndex())
            for k,v in vals.items(): prj.writeEntry(group,k,str(v or ''))
            if vals['workdir']:
                QSettings().setValue('DROPSHydroMX/workdir',vals['workdir'])
                try:
                    os.makedirs(vals['workdir'],exist_ok=True)
                    with open(os.path.join(vals['workdir'],'.dropshydro_session.json'),'w',encoding='utf-8') as fh:
                        json.dump(vals,fh,ensure_ascii=False,indent=2)
                except Exception:
                    pass
        except Exception:
            pass

    def _project_entry(self, key, default=''):
        try:
            v,ok=QgsProject.instance().readEntry('DROPSHydroMX',key,default)
            return v if ok else default
        except Exception:
            return default

    def use_existing_layers(self):
        try:
            dem=self.layer(self.reuse_dem); acc=self.layer(self.reuse_acc); dra=self.layer(self.reuse_dir)
            stream=self.layer(self.reuse_stream); basin=self.layer(self.reuse_basin)
            if dem is not None:
                self.auto.update({'dem_id':dem.id(),'dem_path':self._source_path(dem)})
                self.active_dem_label.setText(self._source_path(dem) or dem.name())
            if acc is not None: self.auto['acc']=self._source_path(acc)
            if dra is not None: self.auto['drainage']=self._source_path(dra)
            if stream is not None:
                self.auto.update({'streams_id':stream.id(),'streams_path':self._source_path(stream)})
                self._build_stream_index(stream)
            if basin is not None: self.auto.update({'basin_id':basin.id(),'basin_path':self._source_path(basin)})
            if not any(x is not None for x in (dem,acc,dra,stream,basin)):
                raise ValueError('Seleccione al menos una capa existente.')
            self._save_session_state(); self.refresh_layers()
            if basin is not None: msg='✓ Cuenca existente cargada. Puede continuar con Morfometría/CHIRPS/Tc.'
            elif stream is not None and dra is not None: msg='✓ Red y dirección reutilizadas. Puede seleccionar el outlet y delimitar la cuenca.'
            elif acc is not None and dra is not None: msg='✓ Acumulación/dirección reutilizadas. Puede generar o cargar la red.'
            else: msg='✓ Resultados existentes incorporados al flujo.'
            self.network_status.setText(msg); self.out.setPlainText('REANUDACIÓN\n\n'+msg)
        except Exception as e:
            QMessageBox.critical(self,'Reutilizar resultados',str(e))

    def recover_existing_results(self, silent=False):
        """Recover outputs from the open project and/or the saved working folder.

        Nothing is recomputed. Existing rasters/vectors are loaded only when needed.
        """
        try:
            saved_dir=self._project_entry('workdir','') or QSettings().value('DROPSHydroMX/workdir','',type=str)
            if saved_dir and os.path.isdir(saved_dir): self.autodir.setText(saved_dir)
            out=self.autodir.text().strip()
            session={}
            if out:
                sp=os.path.join(out,'.dropshydro_session.json')
                try:
                    if os.path.exists(sp):
                        with open(sp,'r',encoding='utf-8') as fh: session=json.load(fh) or {}
                except Exception:
                    session={}
            def stored(key, default=''):
                return self._project_entry(key,'') or str(session.get(key,'') or '') or default
            for name in ('chirps_output','hydro_output','soil_input','cover_input','filled_input','cn_table_input','manning_raster','manning_table'):
                value=stored(name,'')
                if value and hasattr(self,name): getattr(self,name).setText(value)
            if stored('worldcover','') and hasattr(self,'worldcover'): self.worldcover.setChecked(stored('worldcover')=='1')
            if stored('use_basin_inputs','') and hasattr(self,'use_basin_inputs'): self.use_basin_inputs.setChecked(stored('use_basin_inputs')=='1')
            mode=stored('manning_mode','')
            if mode in ('0','1','2') and hasattr(self,'manning_mode'): self.manning_mode.setCurrentIndex(int(mode))
            # Prefer exact saved paths. Fall back to canonical filenames in workdir.
            demp=stored('dem_path','')
            if not (demp and os.path.exists(demp)) and out:
                for cand in [os.path.join(out,'dem_mosaic.tif'),os.path.join(out,'dem_work_utm.tif')]:
                    if os.path.exists(cand): demp=cand; break
            analysisp=stored('analysis_dem_path','')
            filledp=stored('filled_dem_path','')
            if not (filledp and os.path.exists(filledp)) and out:
                hits=sorted(glob.glob(os.path.join(out,'dem_filled_*.tif')), key=lambda p: os.path.getmtime(p), reverse=True)
                if hits: filledp=hits[0]
            accp=stored('acc','')
            if not (accp and os.path.exists(accp)) and out:
                hits=sorted(glob.glob(os.path.join(out,'flow_accumulation_*.tif')), key=lambda p: os.path.getmtime(p), reverse=True)
                if hits: accp=hits[0]
                elif os.path.exists(os.path.join(out,'flow_accumulation.tif')): accp=os.path.join(out,'flow_accumulation.tif')
            drap=stored('drainage','')
            if not (drap and os.path.exists(drap)) and out:
                hits=sorted(glob.glob(os.path.join(out,'flow_direction_*.tif')), key=lambda p: os.path.getmtime(p), reverse=True)
                if hits: drap=hits[0]
                elif os.path.exists(os.path.join(out,'flow_direction.tif')): drap=os.path.join(out,'flow_direction.tif')
            streamp=stored('streams_path','')
            if not (streamp and os.path.exists(streamp)) and out:
                hits=sorted(glob.glob(os.path.join(out,'streams_lines_*.gpkg')), key=lambda p: os.path.getmtime(p), reverse=True)
                if hits: streamp=hits[0]
            basinp=stored('basin_path','')
            if not (basinp and os.path.exists(basinp)) and out:
                hits=sorted([p for p in glob.glob(os.path.join(out,'basin_*.gpkg')) if 'basin_all_' not in os.path.basename(p) and 'polygonize' not in os.path.basename(p)], key=lambda p: os.path.getmtime(p), reverse=True)
                if hits: basinp=hits[0]
                elif os.path.exists(os.path.join(out,'basin.gpkg')): basinp=os.path.join(out,'basin.gpkg')

            dem=self._load_raster_once(demp,'DH_MDE_Recuperado') if demp else None
            analysis_dem=self._load_raster_once(analysisp,'DH_MDE_Analisis_Recuperado') if analysisp and os.path.exists(analysisp) else None
            filled_dem=self._load_raster_once(filledp,'DH_MDE_Filled_Recuperado') if filledp and os.path.exists(filledp) else None
            acc=self._load_raster_once(accp,'DH_Acumulacion') if accp and os.path.exists(accp) else self._find_named_layer(['DH_Acumulacion'],'raster')
            dra=self._load_raster_once(drap,'DH_Direccion') if drap and os.path.exists(drap) else self._find_named_layer(['DH_Direccion'],'raster')
            stream=self._load_vector_once(streamp,'DH_RedDrenaje_Lineas',1) if streamp and os.path.exists(streamp) else self._find_named_layer(['DH_RedDrenaje_Lineas'],'line')
            basin=self._load_vector_once(basinp,'DH_Cuenca',2) if basinp and os.path.exists(basinp) else self._find_named_layer(['DH_Cuenca'],'poly')
            if dem:
                self.auto.update({'dem_id':dem.id(),'dem_path':self._source_path(dem)}); self.active_dem_label.setText(self._source_path(dem) or dem.name())
            if analysis_dem:
                self.auto.update({'analysis_dem_id':analysis_dem.id(),'analysis_dem_path':self._source_path(analysis_dem)})
            if filled_dem:
                self.auto.update({'filled_dem_id':filled_dem.id(),'filled_dem_path':self._source_path(filled_dem)})
            if acc: self.auto['acc']=self._source_path(acc)
            if dra: self.auto['drainage']=self._source_path(dra)
            if stream:
                self.auto.update({'streams_id':stream.id(),'streams_path':self._source_path(stream)}); self._build_stream_index(stream)
            if basin: self.auto.update({'basin_id':basin.id(),'basin_path':self._source_path(basin)})
            clipped_path=stored('basin_streams_path','')
            if clipped_path and os.path.exists(clipped_path.split('|')[0]):
                clipped=self._find_named_layer(['DH_Red_Cuenca_Strahler'],'line')
                if clipped is None or clipped.source()!=clipped_path:
                    clipped=QgsVectorLayer(clipped_path,'DH_Red_Cuenca_Strahler','ogr')
                    if clipped.isValid(): QgsProject.instance().addMapLayer(clipped)
                if clipped.isValid(): self.auto.update(basin_streams_id=clipped.id(),basin_streams_path=clipped_path)

            products=json.loads(stored('basin_rasters','{}') or '{}')
            for name,path in products.items():
                if os.path.isfile(path):
                    raster=self._load_raster_once(path,'DH_'+name+'_Cuenca')
                    if raster and name=='mde': self.auto['basin_dem_id']=raster.id()
            self.auto['basin_rasters']=products
            self.auto['basin_products_key']=stored('basin_products_key','')
            self._sync_basin_inputs()

            # Recover outlet from project layer first; otherwise recover saved coordinates.
            ol=self._find_named_layer(['DH_Outlet'])
            if isinstance(ol,QgsVectorLayer) and ol.geometryType()==0:
                f=next(ol.getFeatures(),None)
                if f is not None and f.geometry() and not f.geometry().isEmpty():
                    pt=f.geometry().asPoint(); self.outlet_stream=QgsPointXY(pt); self.outlet_crs=ol.crs(); self.outlet_layer_id=ol.id()
                    self.outletlabel.setText(f'{pt.x():.3f}, {pt.y():.3f} ({ol.crs().authid()}) — recuperado')
            else:
                xs=stored('outlet_x',''); ys=stored('outlet_y',''); cs=stored('outlet_crs','')
                try:
                    if xs!='' and ys!='' and cs:
                        pt=QgsPointXY(float(xs),float(ys)); crs=QgsCoordinateReferenceSystem(cs)
                        if crs.isValid():
                            self.outlet_stream=pt; self.outlet_crs=crs; self._show_single_outlet(pt,crs)
                            self.outletlabel.setText(f'{pt.x():.3f}, {pt.y():.3f} ({crs.authid()}) — recuperado')
                except Exception: pass
            try:
                tv=int(float(stored('threshold',self.threshold.value()))); self.threshold.setValue(tv)
            except Exception: pass
            self.refresh_layers(); self._save_session_state()
            recovered=[]
            if dem: recovered.append('MDE')
            if filled_dem: recovered.append('MDE acondicionado')
            if acc: recovered.append('acumulación')
            if dra: recovered.append('dirección')
            if stream: recovered.append('red lineal')
            if basin: recovered.append('cuenca')
            if self.outlet_stream is not None: recovered.append('outlet')
            if recovered:
                if basin: self.network_status.setText('✓ Sesión recuperada hasta cuenca. Puede continuar con análisis posteriores.')
                elif stream: self.network_status.setText('✓ Sesión recuperada hasta red. Puede seleccionar/reutilizar outlet y delimitar.')
                elif acc and dra: self.network_status.setText('✓ Sesión recuperada hasta acumulación/dirección. Puede generar la red.')
                if not silent: QMessageBox.information(self,'Reanudar', 'Recuperados sin recalcular: '+', '.join(recovered)+'.')
            elif not silent:
                QMessageBox.information(self,'Reanudar','No encontré resultados previos en el proyecto ni en la carpeta de trabajo actual.')
        except Exception as e:
            if not silent: QMessageBox.warning(self,'Reanudar',str(e))

    def _alg(self,suffix):
        for a in QgsApplication.processingRegistry().algorithms():
            if a.id().endswith(':'+suffix) or a.id()==suffix: return a.id()
        return None
    def _progress_update(self, value=None, message=None):
        """GUI-thread-only progress renderer."""
        if not hasattr(self,'_elapsed'): return
        sec=self._elapsed.elapsed()/1000.0
        if value is not None:
            self._got_numeric_progress=True
            v=max(0,min(100,int(value))); self.progress.setRange(0,100); self.progress.setValue(v)
            self.progress.setFormat(f'{self._stage} — %p%')
        if message:
            self._progress_message=str(message).replace('\n',' ')[:180]
        if getattr(self,'_progress_message',''):
            short=self._progress_message
            self.progress_label.setText(f'{self._stage} | {sec:.1f} s | {short}')
        else:
            self.progress_label.setText(f'{self._stage} | tiempo transcurrido: {sec:.1f} s')

    def _poll_processing_feedback(self):
        """Poll Processing feedback from the GUI thread; never from a worker callback."""
        fb=getattr(self,'_current_feedback',None)
        if fb is None:
            if hasattr(self,'_elapsed'):
                self._progress_update(None,None)
            return
        try:
            value,message,serial=fb.snapshot()
            msg=message if serial!=getattr(self,'_feedback_serial_seen',-1) else None
            if serial!=getattr(self,'_feedback_serial_seen',-1): self._feedback_serial_seen=serial
            self._progress_update(value,msg)
        except Exception:
            pass

    def _start_progress(self, stage):
        self._progress_message=''
        self._stage=stage; self._elapsed=QElapsedTimer(); self._elapsed.start(); self._got_numeric_progress=False
        self.progress.setRange(0,0); self.progress.setFormat(stage+' — procesando…')
        self.progress_label.setText(stage+' — procesando. El tiempo transcurrido continuará actualizándose mientras la tarea esté en segundo plano.')
        self._feedback_serial_seen=-1
        if hasattr(self,'_progress_timer') and not self._progress_timer.isActive(): self._progress_timer.start()

    def _finish_progress(self, ok=True):
        self._stage=getattr(self,'_stage','Proceso')
        try: self._poll_processing_feedback()
        except Exception: pass
        sec=self._elapsed.elapsed()/1000.0 if hasattr(self,'_elapsed') else 0
        self.progress.setRange(0,100); self.progress.setValue(100 if ok else 0)
        self.progress.setFormat(('Completado' if ok else 'Error')+' — %p%')
        self.progress_label.setText(f'{self._stage} — '+('completado' if ok else 'interrumpido')+f' en {sec:.1f} s')
        if hasattr(self,'_progress_timer'): self._progress_timer.stop()
        self._current_feedback=None

    def _run(self,suffix,params,stage=None):
        # QGIS 3.4x/Python 3.12 on Windows can accidentally load NumPy from the
        # per-user Python site-packages instead of the NumPy bundled with QGIS.
        if sys.stderr is None: sys.stderr=io.StringIO()
        if sys.stdout is None: sys.stdout=io.StringIO()
        bad=[p for p in list(sys.path) if ('AppData' in p and 'Roaming' in p and 'Python' in p and 'site-packages' in p)]
        for p in bad:
            try: sys.path.remove(p)
            except ValueError: pass
        import processing
        alg=self._alg(suffix)
        if not alg: raise ValueError('No se encontró el algoritmo '+suffix+'. Revise que los proveedores GRASS/GDAL estén activos en Processing.')
        if stage: self._start_progress(stage)
        fb=DHProgressFeedback(); self._current_feedback=fb
        try:
            # Synchronous Processing calls intentionally do NOT pump Qt events.
            # Re-entering the event loop from a GDAL/GRASS feedback callback caused
            # access-violation crashes on QGIS 3.44/Qt 5.15 under Windows.
            result=processing.run(alg,params,feedback=fb)
            if stage: self._finish_progress(True)
            return result
        except Exception:
            if stage: self._finish_progress(False)
            raise

    def _validate_selected_dems(self):
        if not self.dem_files: raise ValueError('Seleccione al menos un archivo MDE.')
        # ESRI BIL requires its sidecar header (.hdr) in the same folder.
        missing_hdr=[]
        for p in self.dem_files:
            if p.lower().endswith('.bil'):
                hdr=os.path.splitext(p)[0]+'.hdr'
                hdr_upper=os.path.splitext(p)[0]+'.HDR'
                if not (os.path.exists(hdr) or os.path.exists(hdr_upper)):
                    missing_hdr.append(os.path.basename(p))
        if missing_hdr:
            raise ValueError('Archivo BIL sin encabezado .hdr asociado: '+', '.join(missing_hdr)+'. Mantenga .bil y .hdr juntos en la misma carpeta.')
        layers=[QgsRasterLayer(p,os.path.basename(p)) for p in self.dem_files]
        if any(not x.isValid() for x in layers): raise ValueError('Uno o más MDE no pudieron abrirse.')
        return layers

    def _set_active_dem(self,path,label='DH_MDE_Trabajo'):
        lyr=QgsRasterLayer(path,label)
        if not lyr.isValid(): raise ValueError('El MDE activo no pudo abrirse.')
        QgsProject.instance().addMapLayer(lyr)
        self.auto.update({'dem_id':lyr.id(),'dem_path':path})
        self.active_dem_label.setText(path)
        self.network_status.setText('✓ MDE activo listo. Continúe con dirección + acumulación.')
        self.refresh_layers(); self.iface.mapCanvas().setExtent(lyr.extent()); self.iface.mapCanvas().refresh()
        self._save_session_state()
        return lyr

    def use_single_dem(self):
        try:
            layers=self._validate_selected_dems()
            if len(layers)!=1: raise ValueError('Para omitir el mosaico seleccione exactamente un MDE.')
            self._set_active_dem(self.dem_files[0])
        except Exception as e: QMessageBox.critical(self,'MDE activo',str(e))

    def merge_dems(self):
        """Create a real mosaic using GDAL BuildVRT + Translate.

        Using GDAL directly is more robust than the Processing gdal:merge wrapper
        for BIL/IMG/GeoTIFF tiles on Windows and avoids a false-success state in
        which only one source tile is effectively used. The VRT is retained in
        the working directory for diagnostics.
        """
        try:
            layers=self._validate_selected_dems(); out=self.autodir.text().strip(); os.makedirs(out,exist_ok=True)
            if len(layers)==1:
                self._set_active_dem(self.dem_files[0]);
                self.out.setPlainText('Solo se seleccionó un MDE; se usará directamente sin mosaico.')
                return

            # All tiles must be in the same CRS. Compare the complete CRS, not
            # only authid(), because some BIL/DEM files have an empty EPSG id.
            base_crs=layers[0].crs()
            for i,lyr in enumerate(layers[1:],start=2):
                if lyr.crs()!=base_crs:
                    raise ValueError(f'El MDE #{i} tiene un CRS diferente. Reproyecte todos los MDE al mismo CRS antes del mosaico.')

            from osgeo import gdal
            gdal.UseExceptions()
            vrt=os.path.join(out,'dem_mosaic.vrt')
            mosaic=os.path.join(out,'dem_mosaic.tif')
            for oldpath in (vrt,mosaic):
                try:
                    if os.path.exists(oldpath): os.remove(oldpath)
                except Exception:
                    pass

            self._start_progress('Paso 2: construyendo mosaico')
            self.progress_label.setText(f'Paso 2: leyendo {len(self.dem_files)} MDE y construyendo VRT…')

            # Preserve the finest source resolution. BuildVRT only creates a
            # lightweight virtual mosaic, so this stage is normally very fast.
            vrt_opts=gdal.BuildVRTOptions(resolution='highest', resampleAlg='bilinear')
            ds_vrt=gdal.BuildVRT(vrt,self.dem_files,options=vrt_opts)
            if ds_vrt is None:
                raise RuntimeError('GDAL no pudo construir el mosaico virtual (VRT).')
            ds_vrt.FlushCache(); ds_vrt=None

            self.progress.setRange(0,0)
            self.progress_label.setText('Paso 2: materializando GeoTIFF mosaico (puede tardar con archivos grandes)…')

            # Translate the VRT once into a tiled, compressed BigTIFF-safe file.
            tr_opts=gdal.TranslateOptions(
                format='GTiff',
                outputType=gdal.GDT_Float32,
                creationOptions=['TILED=YES','COMPRESS=DEFLATE','PREDICTOR=3','BIGTIFF=IF_SAFER','NUM_THREADS=ALL_CPUS']
            )
            ds_out=gdal.Translate(mosaic,vrt,options=tr_opts)
            if ds_out is None:
                raise RuntimeError('GDAL no pudo materializar el mosaico GeoTIFF.')
            width=int(ds_out.RasterXSize); height=int(ds_out.RasterYSize)
            gt=ds_out.GetGeoTransform(can_return_null=True)
            ds_out.FlushCache(); ds_out=None

            lyr=QgsRasterLayer(mosaic,'DH_MDE_Mosaico')
            if not lyr.isValid():
                raise RuntimeError('El archivo mosaico fue creado, pero QGIS no pudo abrirlo.')

            # Sanity check: the mosaic must cover the union of the input extents.
            xmin=min(x.extent().xMinimum() for x in layers); ymin=min(x.extent().yMinimum() for x in layers)
            xmax=max(x.extent().xMaximum() for x in layers); ymax=max(x.extent().yMaximum() for x in layers)
            e=lyr.extent(); tol=max(abs(lyr.rasterUnitsPerPixelX()),abs(lyr.rasterUnitsPerPixelY()),1e-9)*2
            covers=(e.xMinimum()<=xmin+tol and e.yMinimum()<=ymin+tol and e.xMaximum()>=xmax-tol and e.yMaximum()>=ymax-tol)
            if not covers:
                raise RuntimeError('El mosaico se creó, pero su extensión no cubre todos los MDE de entrada. Revise georreferenciación/CRS de las teselas.')

            self._set_active_dem(mosaic,'DH_MDE_Mosaico')
            self._finish_progress(True)
            rx=abs(lyr.rasterUnitsPerPixelX()); ry=abs(lyr.rasterUnitsPerPixelY())
            self.network_status.setText(f'✓ Mosaico unido: {len(layers)} MDE, {width} × {height} celdas. Continúe con dirección + acumulación.')
            self.out.setPlainText(
                'PASO 2 COMPLETADO\n\n'
                f'MDE unidos: {len(layers)}\n'
                f'Mosaico: {mosaic}\n'
                f'VRT diagnóstico: {vrt}\n'
                f'Dimensiones: {width} × {height} celdas\n'
                f'Resolución: {rx:.4f} × {ry:.4f} unidades/píxel\n'
                f'CRS: {lyr.crs().authid() or lyr.crs().description()}\n\n'
                'El mosaico completo quedó establecido como MDE activo. Continúe con el paso 3.'
            )
        except Exception as e:
            try: self._finish_progress(False)
            except Exception: pass
            self.network_status.setText('✗ Falló la unión de MDE.')
            QMessageBox.critical(self,'Unir MDE',str(e))

    def _working_dem(self):
        if not self.auto.get('dem_path'): raise ValueError('Primero defina el MDE activo (paso 1/2).')
        dem=QgsProject.instance().mapLayer(self.auto.get('dem_id')) or QgsRasterLayer(self.auto['dem_path'],'DH_MDE_Trabajo')
        if not dem.isValid(): raise ValueError('El MDE activo ya no está disponible.')
        if dem.crs().isGeographic():
            out=self.autodir.text(); c=dem.extent().center(); tr=QgsCoordinateTransform(dem.crs(),QgsCoordinateReferenceSystem('EPSG:4326'),QgsProject.instance()); ll=tr.transform(QgsPointXY(c))
            zone=int(math.floor((ll.x()+180)/6)+1); epsg=(32600 if ll.y()>=0 else 32700)+zone
            utm=os.path.join(out,'dem_work_utm.tif')
            self.network_status.setText('Reproyectando MDE a UTM…')
            self._run('warpreproject',{'INPUT':dem.source(),'SOURCE_CRS':dem.crs(),'TARGET_CRS':QgsCoordinateReferenceSystem(f'EPSG:{epsg}'),'RESAMPLING':1,'NODATA':None,'TARGET_RESOLUTION':None,'OPTIONS':'COMPRESS=DEFLATE','DATA_TYPE':5,'TARGET_EXTENT':None,'TARGET_EXTENT_CRS':None,'MULTITHREADING':True,'EXTRA':'','OUTPUT':utm})
            dem=self._set_active_dem(utm,'DH_MDE_UTM')
        return dem

    def _analysis_dem(self):
        dem=self._working_dem(); mode=self.analysis_res.currentText(); out=self.autodir.text(); os.makedirs(out,exist_ok=True)
        cells=max(1,int(dem.width())*int(dem.height())); rx=abs(dem.rasterUnitsPerPixelX()); ry=abs(dem.rasterUnitsPerPixelY()); r=max(rx,ry,1e-9)
        target_res=r
        if mode.startswith('Auto'):
            target_cells=25000000
            if cells>target_cells:
                target_res=r*math.sqrt(cells/target_cells)
                step=5.0 if target_res<100 else 10.0
                target_res=math.ceil(target_res/step)*step
        elif mode!='Original':
            try: target_res=float(mode.split()[0])
            except Exception: target_res=r
        if target_res <= r*1.05:
            self.auto.update({'analysis_dem_path':dem.source(),'analysis_dem_id':dem.id(),'analysis_res':r})
            self.analysis_info.setText(f'Usando resolución original ≈ {r:.2f} m; {cells/1e6:.1f} millones de celdas.')
            return dem
        preview=os.path.join(out,f'dem_preview_{target_res:g}m.vrt')
        est_cells=cells*(r/target_res)**2
        self.analysis_info.setText(f'Remuestreando virtualmente ≈ {r:.2f} m → {target_res:.0f} m: {cells/1e6:.1f} M → ~{est_cells/1e6:.1f} M celdas.')
        if not os.path.exists(preview):
            # Build a warped VRT instead of materialising a large temporary GeoTIFF.
            # This is nearly instantaneous and lets GRASS/GDAL read the coarser view
            # on demand, avoiding an extra full raster write before r.watershed.
            from osgeo import gdal
            gdal.UseExceptions()
            e=dem.extent()
            opts=gdal.WarpOptions(format='VRT', xRes=float(target_res), yRes=float(target_res),
                                  resampleAlg='near', dstSRS=dem.crs().toWkt(),
                                  outputBounds=(e.xMinimum(),e.yMinimum(),e.xMaximum(),e.yMaximum()))
            ds=gdal.Warp(preview,dem.source().split('|')[0],options=opts)
            if ds is None: raise RuntimeError('GDAL no pudo crear el MDE rápido virtual.')
            ds.FlushCache(); ds=None
        pr=QgsRasterLayer(preview,f'DH_MDE_Preview_{target_res:g}m')
        if not pr.isValid(): raise ValueError('No fue posible crear el MDE temporal de resolución rápida.')
        QgsProject.instance().addMapLayer(pr)
        self.auto.update({'analysis_dem_path':preview,'analysis_dem_id':pr.id(),'analysis_res':target_res})
        return pr

    def _flow_dem(self):
        # Prefer the depression-filled DEM used to create the current flow
        # accumulation/direction products. This keeps stream extraction
        # hydrologically consistent with the drainage raster.
        fid=self.auto.get('filled_dem_id'); lyr=QgsProject.instance().mapLayer(fid) if fid else None
        if isinstance(lyr,QgsRasterLayer) and lyr.isValid(): return lyr
        fp=self.auto.get('filled_dem_path')
        if fp and os.path.exists(fp):
            lyr=QgsRasterLayer(fp,'DH_MDE_Filled')
            if lyr.isValid(): return lyr
        aid=self.auto.get('analysis_dem_id'); lyr=QgsProject.instance().mapLayer(aid) if aid else None
        if isinstance(lyr,QgsRasterLayer) and lyr.isValid(): return lyr
        p=self.auto.get('analysis_dem_path')
        if p and os.path.exists(p):
            lyr=QgsRasterLayer(p,'DH_MDE_Analisis')
            if lyr.isValid(): return lyr
        return self._analysis_dem()
    def generate_flow(self):
        """Condition the analysis DEM and calculate drainage/accumulation.

        ArcGeek's robust sequence is reproduced here deliberately:
        resampled DEM -> r.fill.dir -> r.watershed -> drainage + accumulation.
        All subsequent stream extraction and watershed delineation reuse these
        exact products, avoiding grid/DEM inconsistencies.
        """
        try:
            if getattr(self,'_flow_task',None) is not None:
                QMessageBox.information(self,'DROPS-Hydro MX','Ya hay un cálculo hidrológico en ejecución.')
                return
            dem=self._analysis_dem(); out=self.autodir.text(); os.makedirs(out,exist_ok=True)
            run_tag=datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            # Unique outputs avoid stale rasters and Windows file locks from layers
            # generated by previous runs. The session records the exact products.
            acc=os.path.join(out,f'flow_accumulation_{run_tag}.tif'); dra=os.path.join(out,f'flow_direction_{run_tag}.tif')
            filled=os.path.join(out,f'dem_filled_{run_tag}.tif')
            filldir=os.path.join(out,f'dem_fill_direction_{run_tag}.tif')
            fillareas=os.path.join(out,f'dem_fill_areas_{run_tag}.tif')
            mode=self.flow_mode.currentIndex(); use_sfd=(mode in (0,2)); use_disk=(mode==2)

            def start_watershed(flow_dem_path):
                algid=self._alg('r.watershed')
                if not algid: raise ValueError('No se encontró r.watershed. Active el proveedor GRASS en Processing.')
                alg=QgsApplication.processingRegistry().algorithmById(algid)
                if alg is None: raise ValueError('No fue posible cargar r.watershed.')
                # Match ArcGeek's approach: let the input raster define the GRASS
                # computational grid. Do NOT override region/cell size here.
                params={'elevation':flow_dem_path,'threshold':10000,'convergence':5,'memory':self.grass_memory.value(),
                        '-s':use_sfd,'-m':use_disk,'-a':True,
                        'accumulation':acc,'drainage':dra}
                self._start_progress('Paso 3b: dirección y acumulación')
                self.network_status.setText('Calculando r.watershed sobre el MDE acondicionado. SECTION 2: A* Search puede tardar varios minutos.')
                fb=DHProgressFeedback(); self._flow_feedback=fb; self._current_feedback=fb
                ctx=QgsProcessingContext(); ctx.setProject(QgsProject.instance()); self._flow_context=ctx
                task=QgsProcessingAlgRunnerTask(alg,params,ctx,fb); self._flow_task=task
                def watershed_done(success, results):
                    try:
                        self._finish_progress(bool(success))
                        if success and os.path.exists(acc) and os.path.exists(dra):
                            for pth,n in [(acc,'DH_Acumulacion'),(dra,'DH_Direccion')]:
                                self._load_raster_once(pth,n)
                            self.auto.update({'acc':acc,'drainage':dra})
                            self._save_session_state()
                            self.network_status.setText('✓ MDE acondicionado + dirección + acumulación listos. Continúe con la red de drenaje.')
                            self.out.setPlainText(
                                'PASO 3 COMPLETADO\n\n'
                                f'Modo: {self.flow_mode.currentText()}\n'
                                f'MDE usado para flujo: {flow_dem_path}\n'
                                f'Acumulación: {acc}\nDirección: {dra}\n\n'
                                'La red se extraerá usando este mismo MDE acondicionado y esta misma acumulación.'
                            )
                        else:
                            self.network_status.setText('✗ r.watershed terminó sin generar las salidas esperadas. Revise el registro de Processing.')
                    finally:
                        self._flow_task=None
                task.executed.connect(watershed_done)
                QgsApplication.taskManager().addTask(task)

            if not self.fill_sinks.isChecked():
                self.auto.pop('filled_dem_path',None); self.auto.pop('filled_dem_id',None)
                start_watershed(dem.source())
                return

            fillid=self._alg('r.fill.dir')
            if not fillid: raise ValueError('No se encontró r.fill.dir. Active el proveedor GRASS en Processing.')
            fillalg=QgsApplication.processingRegistry().algorithmById(fillid)
            if fillalg is None: raise ValueError('No fue posible cargar r.fill.dir.')
            fillparams={'input':dem.source(),'format':0,'output':filled,'direction':filldir,'areas':fillareas}
            self._start_progress('Paso 3a: rellenando depresiones del MDE')
            self.network_status.setText('Acondicionando MDE con r.fill.dir antes de calcular direcciones de flujo…')
            fb=DHProgressFeedback(); self._flow_feedback=fb; self._current_feedback=fb
            ctx=QgsProcessingContext(); ctx.setProject(QgsProject.instance()); self._flow_context=ctx
            task=QgsProcessingAlgRunnerTask(fillalg,fillparams,ctx,fb); self._flow_task=task
            def fill_done(success, results):
                self._finish_progress(bool(success))
                if not success or not os.path.exists(filled):
                    self._flow_task=None
                    self.network_status.setText('✗ r.fill.dir no produjo el MDE acondicionado.')
                    return
                fl=self._load_raster_once(filled,'DH_MDE_Filled')
                if fl is None or not fl.isValid():
                    self._flow_task=None
                    QMessageBox.critical(self,'Acondicionamiento','r.fill.dir terminó, pero el MDE acondicionado no pudo abrirse.')
                    return
                self.auto.update({'filled_dem_path':filled,'filled_dem_id':fl.id()})
                self._save_session_state()
                self._flow_task=None
                try:
                    start_watershed(filled)
                except Exception as e:
                    self._flow_task=None
                    QMessageBox.critical(self,'Dirección / acumulación',str(e))
            task.executed.connect(fill_done)
            QgsApplication.taskManager().addTask(task)
        except Exception as e:
            self._flow_task=None
            QMessageBox.critical(self,'Dirección / acumulación',str(e))

    def generate_network(self):
        try:
            if not self.auto.get('acc') or not os.path.exists(self.auto['acc']):
                raise ValueError('Primero calcule dirección + acumulación (paso 3).')
            dem=self._flow_dem(); out=self.autodir.text(); os.makedirs(out,exist_ok=True)
            threshold=int(self.threshold.value())
            # Use unique filenames on every run. On Windows an old GeoPackage can remain
            # locked by OGR/QGIS even after a layer is removed from the project.
            run_tag=datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            streamrast=os.path.join(out,f'streams_{threshold}_{run_tag}.tif')
            streamvec=os.path.join(out,f'streams_lines_{threshold}_{run_tag}.gpkg')

            # Remove previous network/outlet before regenerating it.
            old=self.auto.get('streams_id')
            if old:
                try: QgsProject.instance().removeMapLayer(old)
                except Exception: pass
            if self.outlet_layer_id:
                try: QgsProject.instance().removeMapLayer(self.outlet_layer_id)
                except Exception: pass
                self.outlet_layer_id=None
            self.outlet_map=None; self.outlet_stream=None; self.outlet_crs=None; self.stream_index=None
            self.outletlabel.setText('No seleccionado')

            # QGIS/OGR cannot reliably expose the mixed GRASS vector returned by
            # r.stream.extract as a single line layer: the GRASS output contains both
            # stream segments AND point nodes (heads/confluences/outlets).  Therefore we
            # deliberately request the stream RASTER only, then vectorise that already
            # one-cell-wide raster as lines.  This avoids both the mixed-geometry issue
            # and the expensive threshold -> r.thin chain used by the old fallback.
            alg_stream=self._alg('r.stream.extract')
            extracted=False
            if alg_stream:
                try:
                    self.network_status.setText('Extrayendo cauces desde la acumulación existente…')
                    params={'elevation':dem.source(),'accumulation':self.auto['acc'],
                            'threshold':float(threshold),'d8cut':0.0,'mexp':0.0,
                            'stream_length':5,'memory':self.grass_memory.value(),
                            'stream_raster':streamrast}
                    self._run('r.stream.extract',params,stage='Paso 4a: extrayendo red raster')
                    sr=QgsRasterLayer(streamrast,'DH_RedDrenaje_Raster')
                    extracted=bool(os.path.exists(streamrast) and sr.isValid())
                except Exception as fast_err:
                    self.network_status.setText('r.stream.extract no pudo producir el raster; usando método compatible…')
                    extracted=False

            if not extracted:
                # Compatibility fallback: threshold accumulation and thin only when
                # r.stream.extract itself is unavailable/fails.
                raw=os.path.join(out,f'streams_threshold_{threshold}_{run_tag}.tif')
                thin=os.path.join(out,f'streams_thin_{threshold}_{run_tag}.tif')
                self._run('rastercalculator',{'INPUT_A':self.auto['acc'],'BAND_A':1,'INPUT_B':None,'BAND_B':None,'INPUT_C':None,'BAND_C':None,'INPUT_D':None,'BAND_D':None,'INPUT_E':None,'BAND_E':None,'INPUT_F':None,'BAND_F':None,
                                              'FORMULA':f'A>={threshold}','NO_DATA':0,'EXTENT_OPT':0,'INPUT':None,'RTYPE':0,'OPTIONS':'','EXTRA':'','OUTPUT':raw},
                          stage='Paso 4a: umbral de acumulación')
                self._run('r.thin',{'input':raw,'output':thin,'iterations':100},stage='Paso 4b: afinando red')
                streamrast=thin

            # The stream raster from r.stream.extract is already one-cell wide and uses
            # unique integer segment IDs, so it can be converted directly to line
            # geometry.  No additional r.thin is required on the fast path.
            # IMPORTANT (QGIS 3.44 GRASS provider): r.to.vect enum order is
            #   0 = line, 1 = point, 2 = area.
            # Previous releases used type=1 here, which explicitly requested POINTS.
            # Force both the GRASS conversion and v.out.ogr exporter to line geometry.
            vect_params={'input':streamrast,'type':0,'column':'stream_id','output':streamvec,
                         'GRASS_OUTPUT_TYPE_PARAMETER':2}
            self._run('r.to.vect',vect_params,
                      stage='Paso 4c: convirtiendo cauces a líneas')

            sl=QgsVectorLayer(streamvec,'DH_RedDrenaje_Lineas','ogr')
            if not sl.isValid():
                raise ValueError('La red fue extraída como raster, pero QGIS no pudo abrir la conversión vectorial. Revise el registro de Processing de r.to.vect.')
            if QgsWkbTypes.geometryType(sl.wkbType()) != QgsWkbTypes.LineGeometry:
                raise ValueError('La conversión vectorial se creó, pero su geometría no es lineal. El raster de cauces se conservó para diagnóstico: '+streamrast)
            if sl.featureCount() == 0:
                raise ValueError('La red lineal quedó vacía. Reduzca el umbral de acumulación y vuelva a generar la red.')
            try:
                sym=sl.renderer().symbol()
                if hasattr(sym,'setWidth'): sym.setWidth(0.8)
            except Exception: pass
            QgsProject.instance().addMapLayer(sl)
            self._build_stream_index(sl)
            self.auto.update({'stream_raster':streamrast,'streams_id':sl.id(),'streams_path':streamvec})
            self.auto.pop('basin_streams_id',None); self.auto.pop('basin_streams_path',None)
            self._save_session_state()
            method='r.stream.extract raster → r.to.vect' if extracted else 'threshold → r.thin → r.to.vect (respaldo)'
            self.network_status.setText(f'✓ Red LINEAL generada ({sl.featureCount()} segmentos). Haga clic sobre una línea para definir el outlet.')
            self.refresh_layers(); self.iface.mapCanvas().refresh()
            self.out.setPlainText(f'PASO 4 COMPLETADO\n\nMétodo: {method}\nRaster de cauces: {streamrast}\nRed lineal: {streamvec}\nUmbral: {threshold} celdas\nSegmentos: {sl.featureCount()}\n\nLa acumulación NO se recalculó. El outlet se ajustará a la línea más cercana.')
        except Exception as e:
            self.network_status.setText('✗ No fue posible generar la red lineal.')
            QMessageBox.critical(self,'Red de drenaje',str(e))

    def prepare_dem_network(self):
        # Compatibilidad: ya no encadena procesos pesados.
        QMessageBox.information(self,'DROPS-Hydro MX','El procesamiento está separado por etapas; la red se genera como líneas desde la acumulación existente. Use los pasos 1–4 de forma secuencial.')

    def _hydrologic_snap_outlet(self, point, point_crs):
        """Snap a visually selected outlet to an actual drainage-grid cell.

        A vector stream line is only a cartographic representation of the raster
        drainage network.  A point can be geometrically on that line but still
        fall in the neighbouring raster cell.  r.water.outlet then traces from
        the wrong cell and may return only a handful of pixels.

        This routine searches a small neighbourhood around the selected point,
        preferentially keeps cells belonging to the stream raster, and chooses
        the candidate with the largest contributing-area value.  Finally it
        returns the exact centre of the corresponding flow-direction cell.
        """
        acc_path=self.auto.get('acc')
        dra_path=self.auto.get('drainage')
        if not acc_path or not os.path.exists(acc_path):
            raise ValueError('No se encontró el raster de acumulación para ajustar hidrológicamente el outlet.')
        if not dra_path or not os.path.exists(dra_path):
            raise ValueError('No se encontró el raster de dirección para delimitar la cuenca.')

        acc=QgsRasterLayer(acc_path,'DH_Accum_snap')
        dra=QgsRasterLayer(dra_path,'DH_Dir_snap')
        if not acc.isValid() or not dra.isValid():
            raise ValueError('Los rasters de acumulación/dirección no son válidos.')

        # Work in the accumulation CRS while searching for the hydrologically
        # most plausible stream cell around the click.
        if point_crs != acc.crs():
            tr=QgsCoordinateTransform(point_crs,acc.crs(),QgsProject.instance())
            pacc=tr.transform(point)
        else:
            pacc=QgsPointXY(point)

        ext=acc.extent(); w=max(1,acc.width()); h=max(1,acc.height())
        dx=ext.width()/w; dy=ext.height()/h
        col=int(math.floor((pacc.x()-ext.xMinimum())/dx))
        row=int(math.floor((ext.yMaximum()-pacc.y())/dy))
        col=max(0,min(w-1,col)); row=max(0,min(h-1,row))
        radius=max(1,int(self.snapcells.value()))

        stream=None
        sp=self.auto.get('stream_raster')
        if sp and os.path.exists(sp):
            tmp=QgsRasterLayer(sp,'DH_Stream_snap')
            if tmp.isValid() and tmp.crs()==acc.crs() and tmp.width()==w and tmp.height()==h:
                stream=tmp

        ap=acc.dataProvider(); spp=stream.dataProvider() if stream else None
        best=None; best_acc=-float('inf'); best_dist=float('inf')
        # Search stream cells first. If the stream raster cannot be sampled, the
        # maximum accumulation cell is still a robust fallback.
        for rr in range(max(0,row-radius),min(h,row+radius+1)):
            y=ext.yMaximum()-(rr+0.5)*dy
            for cc in range(max(0,col-radius),min(w,col+radius+1)):
                x=ext.xMinimum()+(cc+0.5)*dx
                q=QgsPointXY(x,y)
                if spp is not None:
                    sv,sok=spp.sample(q,1)
                    if (not sok) or sv is None:
                        continue
                    try:
                        if not math.isfinite(float(sv)) or float(sv)<=0:
                            continue
                    except Exception:
                        continue
                av,aok=ap.sample(q,1)
                if not aok or av is None:
                    continue
                try: av=float(av)
                except Exception: continue
                if not math.isfinite(av):
                    continue
                d2=(x-pacc.x())**2+(y-pacc.y())**2
                if av>best_acc or (av==best_acc and d2<best_dist):
                    best_acc=av; best_dist=d2; best=q

        # Fallback: no stream cell found inside the tolerance; search all cells
        # for the local accumulation maximum rather than using the clicked cell.
        if best is None and spp is not None:
            for rr in range(max(0,row-radius),min(h,row+radius+1)):
                y=ext.yMaximum()-(rr+0.5)*dy
                for cc in range(max(0,col-radius),min(w,col+radius+1)):
                    x=ext.xMinimum()+(cc+0.5)*dx
                    q=QgsPointXY(x,y)
                    av,aok=ap.sample(q,1)
                    if not aok or av is None: continue
                    try: av=float(av)
                    except Exception: continue
                    if not math.isfinite(av): continue
                    d2=(x-pacc.x())**2+(y-pacc.y())**2
                    if av>best_acc or (av==best_acc and d2<best_dist):
                        best_acc=av; best_dist=d2; best=q
        if best is None:
            raise ValueError('No se encontró una celda de drenaje válida alrededor del outlet. Aumente la tolerancia de snap o regenere la red con un umbral menor.')

        # Transform to the direction raster CRS, then force the coordinate onto
        # the exact centre of one direction-grid cell. r.water.outlet receives
        # this grid-centred point, not the vector-line coordinate.
        if acc.crs()!=dra.crs():
            tr=QgsCoordinateTransform(acc.crs(),dra.crs(),QgsProject.instance())
            pd=tr.transform(best)
        else:
            pd=QgsPointXY(best)
        de=dra.extent(); dw=max(1,dra.width()); dh=max(1,dra.height())
        ddx=de.width()/dw; ddy=de.height()/dh
        dc=int(math.floor((pd.x()-de.xMinimum())/ddx)); dr=int(math.floor((de.yMaximum()-pd.y())/ddy))
        dc=max(0,min(dw-1,dc)); dr=max(0,min(dh-1,dr))
        snapped=QgsPointXY(de.xMinimum()+(dc+0.5)*ddx,
                           de.yMaximum()-(dr+0.5)*ddy)
        return snapped,dra,best_acc,math.sqrt(best_dist)

    def delineate_from_prepared(self):
        try:
            if not self.auto.get('drainage'): raise ValueError('Primero genere dirección + acumulación (paso 3).')
            if self.fill_sinks.isChecked() and not self.auto.get('filled_dem_path'):
                raise ValueError('La dirección cargada corresponde a una ejecución anterior sin MDE acondicionado. Para esta versión vuelva a ejecutar SOLO el paso 3 (r.fill.dir + r.watershed) y luego regenere la red. No necesita repetir el mosaico.')
            if self.outlet_stream is None and self.outlet_map is None: raise ValueError('Seleccione el punto de salida sobre la red visible.')
            out=self.autodir.text(); os.makedirs(out,exist_ok=True)
            basin_tag=datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')

            # Start from the stream-snapped point, but do not send that floating
            # vector coordinate directly to r.water.outlet. Snap it a second time
            # to the real drainage raster grid using local flow accumulation.
            if self.outlet_stream is not None and self.outlet_crs is not None:
                seed=QgsPointXY(self.outlet_stream); seed_crs=self.outlet_crs
            else:
                seed=QgsPointXY(self.outlet_map); seed_crs=self.iface.mapCanvas().mapSettings().destinationCrs()
            op,dra_layer,accval,shift=self._hydrologic_snap_outlet(seed,seed_crs)

            basinrast=os.path.join(out,f'basin_{basin_tag}.tif')
            # IMPORTANT: use the direction raster's own extent/grid.  The fast
            # DEM VRT can differ by fractions of a cell after resampling, which is
            # enough to shift the outlet when GRASS creates its temporary region.
            self._run('r.water.outlet',{
                'input':self.auto['drainage'],
                'coordinates':f'{op.x()},{op.y()}',
                'output':basinrast
            },stage='Paso 6: delimitando cuenca desde dirección original')

            # r.water.outlet coordinates are in the input direction grid CRS.
            # Persist its full WKT; authid() alone loses custom CRS definitions.
            if not dra_layer.crs().isValid():
                raise ValueError('El raster de dirección no tiene CRS. Defina el CRS de origen antes de delimitar.')
            from osgeo import gdal
            raster=gdal.Open(basinrast,gdal.GA_Update)
            if raster is None: raise ValueError('No se pudo abrir el raster de cuenca.')
            raster.SetProjection(dra_layer.crs().toWkt()); raster=None
            br=QgsRasterLayer(basinrast,'DH_Cuenca_Raster')
            if not br.isValid():
                raise ValueError('r.water.outlet no produjo un raster de cuenca válido.')
            # Count valid basin cells as a sanity check before vectorisation.
            try:
                stats=br.dataProvider().bandStatistics(1,QgsRasterBandStats.All)
                valid_cells=int(stats.elementCount)
            except Exception:
                valid_cells=-1
            if valid_cells>0 and valid_cells<10:
                raise ValueError(f'La cuenca resultante contiene solo {valid_cells} celdas. El outlet fue ajustado a la grilla, pero la dirección de flujo no converge correctamente en ese punto. Pruebe seleccionar un tramo más aguas abajo o ejecutar Fill sinks antes de r.watershed.')

            # Vectorise the basin directly as AREA, following the robust GRASS
            # route used by ArcGeek, then retain the largest polygon in case GRASS
            # exports ancillary pieces.
            basinall=os.path.join(out,f'basin_all_{basin_tag}.gpkg'); basinvec=os.path.join(out,f'basin_{basin_tag}.gpkg')
            try:
                self._run('r.to.vect',{
                    'input':basinrast,'type':2,'column':'value','-s':True,'output':basinall,
                    'GRASS_OUTPUT_TYPE_PARAMETER':3
                },stage='Paso 6b: vectorizando cuenca')
                tmp=QgsVectorLayer(basinall,'DH_Cuenca_all','ogr')
                if not tmp.isValid() or QgsWkbTypes.geometryType(tmp.wkbType())!=QgsWkbTypes.PolygonGeometry:
                    raise RuntimeError('r.to.vect no produjo polígonos válidos')
                # Create a clean memory layer with only the largest polygon, then
                # persist it with native:savefeatures.
                feats=[f for f in tmp.getFeatures() if f.geometry() and not f.geometry().isEmpty()]
                if not feats: raise RuntimeError('La vectorización de la cuenca quedó vacía')
                largest=max(feats,key=lambda f:f.geometry().area())
                mem=QgsVectorLayer('MultiPolygon','DH_Cuenca_largest','memory')
                mem.setCrs(dra_layer.crs())
                mp=mem.dataProvider(); nf=QgsFeature(); nf.setGeometry(largest.geometry()); mp.addFeature(nf); mem.updateExtents()
                self._run('savefeatures',{'INPUT':mem,'OUTPUT':basinvec},stage='Paso 6c: guardando cuenca')
            except Exception:
                # Compatibility fallback for installations where GRASS area
                # export differs: polygonize the basin raster and keep DN=1.
                polyall=os.path.join(out,f'basin_polygonize_{basin_tag}.gpkg')
                self._run('polygonize',{'INPUT':basinrast,'BAND':1,'FIELD':'DN','EIGHT_CONNECTEDNESS':False,'EXTRA':'','OUTPUT':polyall})
                polygonized=QgsVectorLayer(polyall,'basin_polygonized','ogr')
                polygonized.setCrs(dra_layer.crs())
                self._run('extractbyattribute',{'INPUT':polygonized,'FIELD':'DN','OPERATOR':0,'VALUE':1,'OUTPUT':basinvec})

            # Explicit assignment on a fresh file bypasses Processing/provider CRS
            # overrides and avoids cached handles to the intermediate GeoPackage.
            from .basin_products import finalize_basin
            basinvec=finalize_basin(basinvec,os.path.join(out,f'basin_final_{basin_tag}.gpkg'),dra_layer.crs().toWkt())
            bl=QgsVectorLayer(basinvec,'DH_Cuenca','ogr')
            if not bl.isValid() or bl.featureCount()==0:
                raise ValueError('La delimitación terminó, pero no produjo una cuenca vectorial válida.')
            # Disk CRS was verified semantically above; use the exact reference
            # object in QGIS as well (custom CRS IDs need not survive serialization).
            bl.setCrs(dra_layer.crs())
            for key in ('basin_dem_id','basin_rasters','basin_products_key','basin_streams_id','basin_streams_path'):
                self.auto.pop(key,None)
            self._terrain_metrics={}
            QgsProject.instance().addMapLayer(br)
            QgsProject.instance().addMapLayer(bl)

            # Display the true hydrologic outlet as a separate diagnostic point.
            self._show_single_outlet(op,dra_layer.crs())
            self.outlet_stream=QgsPointXY(op); self.outlet_crs=dra_layer.crs()
            self.auto.update({'basin_id':bl.id(),'basin_path':basinvec,
                              'outlet_x':op.x(),'outlet_y':op.y(),'outlet_crs':dra_layer.crs().authid()})
            self._save_session_state(); self.refresh_layers()
            self.network_status.setText('✓ Cuenca delimitada desde outlet ajustado a la grilla de dirección.')
            area=sum(f.geometry().area() for f in bl.getFeatures())
            cell_area=abs(dra_layer.rasterUnitsPerPixelX()*dra_layer.rasterUnitsPerPixelY())
            expected_area=max(0.0,float(accval))*cell_area
            ratio=(area/expected_area) if expected_area>0 else float('nan')
            self.out.setPlainText(
                f'ETAPA 2 COMPLETADA\n\nCuenca: {basinvec}\n'
                f'Outlet hidrológico: {op.x():.3f}, {op.y():.3f} ({dra_layer.crs().authid()})\n'
                f'Ajuste desde línea: {shift:.2f} unidades\nAcumulación en outlet: {accval:.0f} celdas\n'
                f'Área vectorial aproximada: {area/1e6:.3f} km²\n'
                f'Área esperada por acumulación: {expected_area/1e6:.3f} km²\n'
                f'Razón área delineada/esperada: {ratio:.3f}\n\n'
                'Continúe con Morfometría → CHIRPS/extremos → Gastos/Tc → Inundación.'
            )
            full_index=self.streams.findData(self.auto.get('streams_id'))
            if full_index>=0: self.streams.setCurrentIndex(full_index)
            self.start_terrain_analysis(self.out.toPlainText())
        except Exception as e:
            QMessageBox.critical(self,'Delimitación de cuenca',str(e))

    def auto_watershed(self):
        # Compatibilidad con proyectos previos: ejecuta el nuevo flujo por etapas.
        self.prepare_dem_network()
    def _tab_inputs(self):
        w=QWidget(); f=QFormLayout(w); self.basin=self.combo_layers('poly'); self.dem=self.combo_layers('raster',True); self.streams=self.combo_layers('line',True)
        self.orderfield=QLineEdit('Strahler'); self.Lmain=QDoubleSpinBox(); self.Lmain.setRange(0,1000000); self.Lmain.setValue(5000); self.Lmain.setSuffix(' m')
        self.slope=QDoubleSpinBox(); self.slope.setRange(.00001,5); self.slope.setDecimals(5); self.slope.setValue(.02)
        for a,b in [('Cuenca:',self.basin),('MDE:',self.dem),('Red de drenaje:',self.streams),('Campo orden Strahler:',self.orderfield),('Longitud cauce principal (manual / calculada):',self.Lmain),('Pendiente cauce (si no hay MDE):',self.slope)]: f.addRow(a,b)
        self.tabs.addTab(w,'2. Entradas')
    def _tab_morph(self):
        w=QWidget(); v=QVBoxLayout(w); v.addWidget(QLabel('Calcula parámetros geométricos, de forma, relieve y red de drenaje, de forma similar a un análisis morfométrico de cuenca.'))
        b=QPushButton('Calcular morfometría completa'); b.clicked.connect(self.morphometry); v.addWidget(b); self.tabs.addTab(w,'3. Morfometría')
    def _legacy_tab_rain(self):
        w=QWidget(); f=QFormLayout(w); self.y0=QSpinBox(); self.y0.setRange(1981,2026); self.y0.setValue(2006); self.y1=QSpinBox(); self.y1.setRange(1981,2026); self.y1.setValue(2025)
        self.chirps_provider=QComboBox(); self.chirps_provider.addItems(['ClimateSERV CHIRPS v2 — rápido (recomendado)','CHIRPS v3 RNL COG — directo (lento/experimental)'])
        self.chirps_location=QComboBox(); self.chirps_location.addItem('Centroide automático de la cuenca (WGS84)')
        self.chirps_lon=QDoubleSpinBox(); self.chirps_lon.setRange(-180,180); self.chirps_lon.setDecimals(6); self.chirps_lon.setReadOnly(True)
        self.chirps_lat=QDoubleSpinBox(); self.chirps_lat.setRange(-90,90); self.chirps_lat.setDecimals(6); self.chirps_lat.setReadOnly(True)
        self.basin.currentIndexChanged.connect(self._update_chirps_centroid)
        self.tabs.currentChanged.connect(lambda index: self._update_chirps_centroid() if index==3 else None)
        QTimer.singleShot(0,self._update_chirps_centroid)
        self.csvpath=QLineEdit(); bc=QPushButton('Abrir CSV'); bc.clicked.connect(self.pickcsv); h=QHBoxLayout(); h.addWidget(self.csvpath); h.addWidget(bc)
        self.duration=QDoubleSpinBox(); self.duration.setRange(.1,24); self.duration.setValue(24); self.duration.setSuffix(' h')
        self.ddfexp=QDoubleSpinBox(); self.ddfexp.setRange(.1,1); self.ddfexp.setDecimals(2); self.ddfexp.setValue(.33)
        self.rps=QLineEdit('2,5,10,25,50,100')
        f.addRow('Fuente CHIRPS:',self.chirps_provider); f.addRow('Ubicación:',self.chirps_location); f.addRow('Longitud del centroide (°):',self.chirps_lon); f.addRow('Latitud del centroide (°):',self.chirps_lat)
        f.addRow('Inicio:',self.y0); f.addRow('Fin:',self.y1); f.addRow('CSV alternativo (date,precip_mm):',h); f.addRow('Duración de diseño:',self.duration); f.addRow('Exponente DDF (P_d=P24*(d/24)^n):',self.ddfexp); f.addRow('Periodos de retorno (años):',self.rps)
        b1=QPushButton('Descargar CHIRPS y obtener máximos anuales — NO requiere MDE'); b1.clicked.connect(self.read_chirps); f.addRow(b1)
        b2=QPushButton('Leer CSV y calcular máximos anuales'); b2.clicked.connect(self.read_csv); f.addRow(b2)
        b3=QPushButton('Ajustar Gumbel y calcular lluvia de diseño'); b3.clicked.connect(self.fit_returns); f.addRow(b3)
        f.addRow(QLabel('ClimateSERV: solicitudes por año completo, caché local y reanudación. Puede cancelar con el botón inferior.'))
        self.tabs.addTab(w,'4. CHIRPS / extremos')
    def _legacy_tab_hydro(self):
        w=QWidget(); f=QFormLayout(w); self.cn=QDoubleSpinBox(); self.cn.setRange(30,100); self.cn.setValue(80); self.cn.setDecimals(1)
        self.tc=QDoubleSpinBox(); self.tc.setRange(.01,500); self.tc.setValue(1); self.tc.setSuffix(' h')
        self.autotc=QComboBox(); self.autotc.addItems(['Kirpich','Giandotti','Témez','NRCS/SCS lag','Promedio de métodos','Tc manual'])
        self.basinSlope=QDoubleSpinBox(); self.basinSlope.setRange(.01,100); self.basinSlope.setValue(5); self.basinSlope.setSuffix(' %')
        f.addRow('Curve Number:',self.cn); f.addRow('Método Tc adoptado:',self.autotc); f.addRow('Pendiente media cuenca (NRCS, %):',self.basinSlope); f.addRow('Tc manual:',self.tc)
        bt=QPushButton('Comparar métodos de Tc'); bt.clicked.connect(self.compare_tc); f.addRow(bt)
        b=QPushButton('Calcular gastos Qp para todos los periodos de retorno'); b.clicked.connect(self.return_flows); f.addRow(b)
        self.tabs.addTab(w,'5. Gastos / Tc')
    def _legacy_geom_hydro_params(self):
        A,_,_,_=self.basic(); L=max(self.Lmain.value()/1000.0,0.001); sl=max(self.slope.value(),1e-6); H=sl*L*1000.0
        # When DEM is available, use relief as a robust first automatic estimate of H.
        ds=self.dem_stats()
        if ds:
            H=max(ds[1]-ds[0],1.0)
            sl=max(H/(L*1000.0),1e-6)
        return A,L,sl,H
    def _legacy_tc_methods(self):
        A,Lkm,J,H=self._geom_hydro_params(); Lm=Lkm*1000.0; cn=self.cn.value()
        kir=.01947*(Lm**.77)*(J**-.385)/60.0
        gia=(4*math.sqrt(max(A,1e-9))+1.5*Lkm)/(0.8*math.sqrt(max(H,1e-9)))
        tem=.3*((Lkm/(J**0.25))**0.76)
        # NRCS watershed-lag form; L in ft, Y in %, retention S in inches. Tc=lag/0.6.
        Lft=Lm*3.28084; Y=max(self.basinSlope.value(),.01); Sin=1000.0/cn-10.0
        nrcs=(Lft**0.8*((Sin+1.0)**0.7))/(1140.0*math.sqrt(Y))
        return {'Kirpich':kir,'Giandotti':gia,'Témez':tem,'NRCS/SCS lag':nrcs}
    def _legacy_compare_tc(self):
        try:
            A,L,J,H=self._geom_hydro_params(); vals=self.tc_methods(); lines=['TIEMPOS DE CONCENTRACIÓN',f'A={A:.3f} km²; L={L:.3f} km; H={H:.1f} m; J={J:.5f} m/m; CN={self.cn.value():.1f}']
            for k,v in vals.items(): lines.append(f'{k:16s}: {v:.3f} h  ({v*60:.1f} min)')
            lines.append(f'Promedio         : {statistics.mean(vals.values()):.3f} h')
            lines.append('\nNo se recomienda escoger el menor Tc automáticamente. Cada ecuación tiene dominio de aplicación distinto; compare resultados y documente el método adoptado.')
            self.out.setPlainText('\n'.join(lines))
        except Exception as e: QMessageBox.critical(self,'Tc',str(e))
    def selected_tc(self):
        name=self.autotc.currentText()
        if name=='Tc manual': return self.tc.value()
        vals=self.tc_methods()
        if name=='Promedio de métodos': return statistics.mean(vals.values())
        return vals[name]
    def _tab_drought(self):
        w=QWidget(); f=QFormLayout(w)
        self.spi_scale=QComboBox(); self.spi_scale.addItems(['1','3','6','12']); self.spi_scale.setCurrentText('3')
        f.addRow('Escala acumulada (meses):',self.spi_scale)
        b=QPushButton('Calcular sequía con CHIRPS (índice estandarizado empírico)'); b.clicked.connect(self.drought_index); f.addRow(b)
        f.addRow(QLabel('Usa la serie diaria CHIRPS ya descargada, la agrega mensualmente y estandariza acumulados por mes calendario. No requiere recalcular el MDE.'))
        self.tabs.addTab(w,'7. Sequías')

    def _monthly_precip(self, series=None):
        series=series if series is not None else self.chirps_daily
        mon={}
        for d,v in series: mon[(d.year,d.month)]=mon.get((d.year,d.month),0.0)+max(0.0,float(v))
        return sorted((datetime.date(y,m,1),v) for (y,m),v in mon.items())

    def drought_index(self):
        try:
            monthly=self._monthly_precip()
            if len(monthly)<36: raise ValueError('Descargue primero una serie CHIRPS suficientemente larga; se recomiendan al menos 3 años y preferentemente ≥30 años.')
            scale=int(self.spi_scale.currentText()); rolled=[]
            vals=[v for _,v in monthly]
            for i,(d,v) in enumerate(monthly):
                if i+1>=scale: rolled.append((d,sum(vals[i-scale+1:i+1])))
            bymonth={m:[] for m in range(1,13)}
            for d,v in rolled: bymonth[d.month].append(v)
            normal=statistics.NormalDist(); rows=[]
            for d,v in rolled:
                ref=sorted(bymonth[d.month]); n=len(ref)
                # Average rank handles ties sufficiently for screening.
                lo=sum(1 for x in ref if x<v); eq=sum(1 for x in ref if x==v); rank=lo+(eq+1)/2
                p=(rank-0.44)/(n+0.12); p=min(max(p,0.001),0.999); z=normal.inv_cdf(p)
                if z<=-2: cls='Extrema'
                elif z<=-1.5: cls='Severa'
                elif z<=-1: cls='Moderada'
                elif z<1: cls='Normal'
                elif z<1.5: cls='Húmeda'
                else: cls='Muy húmeda'
                rows.append((d,v,z,cls))
            outdir=self.autodir.text(); os.makedirs(outdir,exist_ok=True); path=os.path.join(outdir,f'drought_index_{scale}m.csv')
            with open(path,'w',newline='',encoding='utf-8') as fh:
                wr=csv.writer(fh); wr.writerow(['month',f'precip_{scale}m_mm','standardized_index','class']); wr.writerows([(d.isoformat(),f'{v:.3f}',f'{z:.3f}',c) for d,v,z,c in rows])
            tail=rows[-24:]; self.out.setPlainText(f'SEQUÍA — índice estandarizado empírico {scale} meses\nCSV: {path}\n\nMes | P acum. | Índice | Clase\n'+'\n'.join(f'{d:%Y-%m} | {v:7.1f} | {z:6.2f} | {c}' for d,v,z,c in tail)+"\n\nNota: es un índice empírico por rangos normales, útil para screening. No sustituye un SPI paramétrico gamma/SPEI para análisis normativo o publicación sin validación.")
        except Exception as e: QMessageBox.critical(self,'Sequías',str(e))

    def _tab_ml(self):
        w=QWidget(); f=QFormLayout(w)
        self.ml_target_area=QDoubleSpinBox(); self.ml_target_area.setRange(0.01,1000); self.ml_target_area.setValue(0.5); self.ml_target_area.setDecimals(2); self.ml_target_area.setSuffix(' km²')
        f.addRow('Área inicial mínima de drenaje:',self.ml_target_area)
        b1=QPushButton('IA/ML: recomendar threshold de red'); b1.clicked.connect(self.recommend_threshold); f.addRow(b1)
        b2=QPushButton('Guardar threshold actual como validado (aprendizaje local)'); b2.clicked.connect(self.save_threshold_case); f.addRow(b2)
        self.ml_csv=QLineEdit(); bcsv=QPushButton('CSV estación'); bcsv.clicked.connect(self.pick_ml_csv); hh=QHBoxLayout(); hh.addWidget(self.ml_csv); hh.addWidget(bcsv); f.addRow('Estación observada (date,precip_mm):',hh)
        b3=QPushButton('ML: corregir sesgo de CHIRPS con estación + validar'); b3.clicked.connect(self.ml_bias_correction); f.addRow(b3)
        b4=QPushButton('Usar CHIRPS-ML corregido para extremos'); b4.clicked.connect(self.use_ml_corrected); f.addRow(b4)
        f.addRow(QLabel('El recomendador aprende de thresholds que usted valida. La corrección CHIRPS usa una regresión estacional ligera sin NumPy/scikit-learn para evitar conflictos con Python de QGIS.'))
        self.tabs.addTab(w,'8. IA / ML')

    def _threshold_training_path(self):
        return os.path.join(QgsApplication.qgisSettingsDirPath(),'DROPSHydroMX_threshold_training.json')

    def _threshold_features(self):
        dem=self._flow_dem(); res=max(abs(dem.rasterUnitsPerPixelX()),abs(dem.rasterUnitsPerPixelY()),1e-6); cells=max(1,dem.width()*dem.height())
        try:
            st=dem.dataProvider().bandStatistics(1,(QgsRasterBandStats.Min | QgsRasterBandStats.Max | QgsRasterBandStats.Mean),dem.extent(),0); relief=max(0.0,st.maximumValue-st.minimumValue)
        except Exception: relief=0.0
        return res,float(cells),relief

    def recommend_threshold(self):
        try:
            res,cells,relief=self._threshold_features(); area=self.ml_target_area.value(); heuristic=max(10,int(round(area*1e6/(res*res))))
            cases=[]; path=self._threshold_training_path()
            if os.path.exists(path):
                try: cases=json.loads(open(path,encoding='utf-8').read())
                except Exception: cases=[]
            if len(cases)>=3:
                x=(math.log(max(res,1e-6)),math.log(max(cells,1)),math.log(relief+1))
                scored=[]
                for c in cases:
                    z=(math.log(max(c['res'],1e-6)),math.log(max(c['cells'],1)),math.log(c.get('relief',0)+1)); dist=math.sqrt(sum((a-b)**2 for a,b in zip(x,z))); scored.append((dist,c['threshold']))
                scored.sort(); k=min(5,len(scored)); num=den=0.0
                for dist,t in scored[:k]: w=1.0/(dist+0.05); num+=w*t; den+=w
                rec=max(10,int(round(num/den))); method=f'k-NN local con {k} casos cercanos ({len(cases)} casos validados)'
            else:
                rec=heuristic; method=f'bootstrap hidrológico por área mínima; ML se activa al guardar ≥3 casos validados (actuales: {len(cases)})'
            self.threshold.setValue(min(rec,self.threshold.maximum()))
            self.out.setPlainText(f'RECOMENDACIÓN DE THRESHOLD\n\nResolución: {res:.2f} m\nCeldas: {cells/1e6:.2f} M\nRelieve aprox.: {relief:.1f} m\nThreshold recomendado y aplicado: {rec} celdas\nEquivale a ~{rec*res*res/1e6:.3f} km² de área aportante\nMétodo: {method}')
        except Exception as e: QMessageBox.critical(self,'IA/ML threshold',str(e))

    def save_threshold_case(self):
        try:
            res,cells,relief=self._threshold_features(); path=self._threshold_training_path(); cases=[]
            if os.path.exists(path):
                try: cases=json.loads(open(path,encoding='utf-8').read())
                except Exception: cases=[]
            cases.append({'res':res,'cells':cells,'relief':relief,'threshold':int(self.threshold.value()),'date':datetime.datetime.now().isoformat(timespec='seconds')}); cases=cases[-100:]
            with open(path,'w',encoding='utf-8') as fh: json.dump(cases,fh,indent=2)
            self.out.setPlainText(f'Caso guardado para aprendizaje local.\nCasos disponibles: {len(cases)}\nArchivo: {path}')
        except Exception as e: QMessageBox.critical(self,'IA/ML threshold',str(e))

    def pick_ml_csv(self):
        p,_=QFileDialog.getOpenFileName(self,'CSV de estación observada','','CSV (*.csv)')
        if p: self.ml_csv.setText(p)

    @staticmethod
    def _solve_linear(A,b):
        n=len(b); M=[list(map(float,A[i]))+[float(b[i])] for i in range(n)]
        for col in range(n):
            pivot=max(range(col,n),key=lambda r:abs(M[r][col]))
            if abs(M[pivot][col])<1e-12: M[pivot][col]=1e-12
            M[col],M[pivot]=M[pivot],M[col]; pv=M[col][col]; M[col]=[x/pv for x in M[col]]
            for r in range(n):
                if r==col: continue
                fac=M[r][col]
                if fac: M[r]=[a-fac*c for a,c in zip(M[r],M[col])]
        return [M[i][-1] for i in range(n)]

    def _fit_seasonal_regression(self, rows):
        # Features: intercept, CHIRPS, sqrt(CHIRPS), annual sine/cosine. Ridge stabilizes collinearity.
        X=[]; y=[]
        for d,c,o in rows:
            ang=2*math.pi*d.timetuple().tm_yday/365.25; X.append([1.0,c,math.sqrt(max(c,0)),math.sin(ang),math.cos(ang)]); y.append(o)
        p=len(X[0]); A=[[0.0]*p for _ in range(p)]; b=[0.0]*p
        for xi,yi in zip(X,y):
            for i in range(p):
                b[i]+=xi[i]*yi
                for j in range(p): A[i][j]+=xi[i]*xi[j]
        ridge=1e-5
        for i in range(1,p): A[i][i]+=ridge
        return self._solve_linear(A,b)

    @staticmethod
    def _reg_predict(beta,d,c):
        ang=2*math.pi*d.timetuple().tm_yday/365.25; x=[1.0,c,math.sqrt(max(c,0)),math.sin(ang),math.cos(ang)]; return max(0.0,sum(a*b for a,b in zip(beta,x)))

    def ml_bias_correction(self):
        try:
            if not self.chirps_daily: raise ValueError('Descargue CHIRPS primero. El módulo ML no requiere MDE.')
            p=self.ml_csv.text().strip()
            if not p or not os.path.exists(p): raise ValueError('Seleccione un CSV de estación con columnas date,precip_mm.')
            obs={}
            with open(p,encoding='utf-8-sig') as fh:
                for row in csv.DictReader(fh): obs[datetime.date.fromisoformat(row['date'])]=float(row['precip_mm'])
            aligned=[(d,float(c),obs[d]) for d,c in self.chirps_daily if d in obs and obs[d]>=0]
            if len(aligned)<100: raise ValueError(f'Solo hay {len(aligned)} fechas coincidentes; se recomiendan al menos 100.')
            aligned.sort(key=lambda r:r[0]); cut=max(50,int(len(aligned)*0.8)); train=aligned[:cut]; test=aligned[cut:]
            beta=self._fit_seasonal_regression(train); pred=[self._reg_predict(beta,d,c) for d,c,o in test]; actual=[o for d,c,o in test]
            mae=sum(abs(a-b) for a,b in zip(actual,pred))/len(actual); rmse=math.sqrt(sum((a-b)**2 for a,b in zip(actual,pred))/len(actual)); mean=statistics.mean(actual); sst=sum((a-mean)**2 for a in actual); sse=sum((a-b)**2 for a,b in zip(actual,pred)); r2=1-sse/sst if sst>0 else float('nan')
            self.chirps_corrected_daily=[(d,self._reg_predict(beta,d,c)) for d,c in self.chirps_daily]
            outdir=self.autodir.text(); os.makedirs(outdir,exist_ok=True); op=os.path.join(outdir,'chirps_ml_bias_corrected.csv')
            raw=dict(self.chirps_daily)
            with open(op,'w',newline='',encoding='utf-8') as fh:
                wr=csv.writer(fh); wr.writerow(['date','chirps_mm','chirps_ml_corrected_mm']); wr.writerows([(d.isoformat(),f'{raw[d]:.4f}',f'{v:.4f}') for d,v in self.chirps_corrected_daily])
            self.out.setPlainText(f'ML — CORRECCIÓN DE SESGO CHIRPS\n\nModelo: regresión estacional [CHIRPS, sqrt(CHIRPS), sin/cos día del año]\nEntrenamiento: {len(train)} días; prueba temporal: {len(test)} días\nMAE={mae:.3f} mm/d\nRMSE={rmse:.3f} mm/d\nR²={r2:.3f}\nCoeficientes={beta}\n\nSerie corregida: {op}\n\nUse “Usar CHIRPS-ML corregido para extremos” solo después de revisar las métricas y la representatividad de la estación.')
        except Exception as e: QMessageBox.critical(self,'ML CHIRPS',str(e))

    def use_ml_corrected(self):
        try:
            if not self.chirps_corrected_daily: raise ValueError('Primero entrene y valide la corrección ML.')
            annual={}
            for d,v in self.chirps_corrected_daily: annual[d.year]=max(annual.get(d.year,-1.0),v)
            self.chirps_series=sorted(annual.items()); self.out.setPlainText('Serie CHIRPS-ML activada para análisis de extremos.\nMáximos anuales:\n'+'\n'.join(f'{y}: {v:.2f} mm' for y,v in self.chirps_series))
        except Exception as e: QMessageBox.critical(self,'ML CHIRPS',str(e))

    def _legacy_tab_flood(self):
        w=QWidget(); f=QFormLayout(w); self.floodrp=QComboBox(); self.floodrp.addItems(['2','5','10','25','50','100']); self.manning=QDoubleSpinBox(); self.manning.setRange(.01,.3); self.manning.setValue(.05); self.manning.setDecimals(3)
        self.simmins=QSpinBox(); self.simmins.setRange(5,1440); self.simmins.setValue(120); self.outdir=QLineEdit(os.path.join(tempfile.gettempdir(),'DROPSHydroMX'))
        bo=QPushButton('Carpeta'); bo.clicked.connect(self.pickdir); h=QHBoxLayout(); h.addWidget(self.outdir); h.addWidget(bo)
        f.addRow('Periodo de retorno:',self.floodrp); f.addRow("Manning n:",self.manning); f.addRow('Iteraciones/tiempo de simulación:',self.simmins); f.addRow('Salida:',h)
        b=QPushButton('Generar mapas de profundidad y descarga (GRASS r.sim.water)'); b.clicked.connect(self.flood); f.addRow(b)
        self.tabs.addTab(w,'6. Inundación')

    @staticmethod
    def _union_geometry(lyr):
        request=QgsFeatureRequest().setSubsetOfAttributes([])
        geometries=[ft.geometry() for ft in lyr.getFeatures(request)
                    if ft.hasGeometry() and not ft.geometry().isEmpty()]
        if not geometries: raise ValueError('La cuenca seleccionada está vacía.')
        g=QgsGeometry.unaryUnion(geometries)
        if g.isNull() or g.isEmpty():
            raise ValueError('No se pudo unir la cuenca: '+g.lastError())
        return g

    def basin_geom(self):
        lyr=self.layer(self.basin)
        if not lyr: raise ValueError('Seleccione una cuenca.')
        if not lyr.crs().isValid(): raise ValueError('La cuenca no tiene CRS. Vuelva a delimitarla con el raster de dirección georreferenciado.')
        g=self._union_geometry(lyr)
        c=g.centroid().asPoint(); tr=QgsCoordinateTransform(lyr.crs(),QgsCoordinateReferenceSystem('EPSG:4326'),QgsProject.instance()); ll=tr.transform(QgsPointXY(c))
        zone=int(math.floor((ll.x()+180)/6)+1); epsg=(32600 if ll.y()>=0 else 32700)+zone; crs=QgsCoordinateReferenceSystem('EPSG:%d'%epsg)
        gu=QgsGeometry(g); gu.transform(QgsCoordinateTransform(lyr.crs(),crs,QgsProject.instance()))
        return lyr,g,gu,ll,epsg
    def basic(self):
        lyr,g,gu,ll,epsg=self.basin_geom(); return gu.area()/1e6,gu.length()/1000,ll,epsg
    def dem_stats(self):
        dem=self.layer(self.dem)
        if not dem: return None
        metrics=getattr(self,'_terrain_metrics',{})
        basin=self.layer(self.basin)
        if basin and metrics.get('basin_key')==self._union_geometry(basin).asWkt()+'|'+basin.crs().toWkt():
            return metrics['z_min'],metrics['z_max'],metrics['z_mean']
        # Do not report elevations outside the basin while its analysis is pending.
        return None
    def morphometry(self):
        self._morphometry(True)

    def _morphometry(self, calculate_terrain):
        try:
            _,_,basin_metric,ll,epsg=self.basin_geom()
            A=basin_metric.area()/1e6; P=basin_metric.length()/1000
            L=self.Lmain.value()/1000.0; stream=self.layer(self.streams); totalL=0; Nu=0; orders={}
            if stream:
                tr=QgsCoordinateTransform(stream.crs(),QgsCoordinateReferenceSystem('EPSG:%d'%epsg),QgsProject.instance())
                field=self.orderfield.text().strip(); idx=stream.fields().indexOf(field)
                lengths=[]
                for ft in stream.getFeatures():
                    gg=ft.geometry(); gg.transform(tr); gg=gg.intersection(basin_metric)
                    if gg.isEmpty() or gg.length()<=0: continue
                    lm=gg.length()/1000.; totalL+=lm; Nu+=1; lengths.append(lm)
                    if idx>=0:
                        try: o=int(ft[field]); orders[o]=orders.get(o,0)+1
                        except: pass
            Rc=4*math.pi*A/(P*P) if P else float('nan'); Kc=P/(2*math.sqrt(math.pi*A)) if A else float('nan'); Rf=A/(L*L) if L else float('nan'); Re=2*math.sqrt(A/math.pi)/L if L else float('nan')
            Dd=totalL/A if A and totalL else float('nan'); Fs=Nu/A if A and Nu else float('nan'); bif=[]
            for o in sorted(orders):
                if o+1 in orders and orders[o+1]>0: bif.append(orders[o]/orders[o+1])
            Rb=sum(bif)/len(bif) if bif else float('nan'); ds=self.dem_stats(); relief=smean=HI=Rr=Rn=float('nan')
            if ds:
                zmin,zmax,zmean=ds; relief=zmax-zmin; HI=(zmean-zmin)/relief if relief else float('nan'); Rr=relief/(L*1000) if L else float('nan'); Rn=(relief/1000)*Dd if not math.isnan(Dd) else float('nan')
            txt=f'''MORFOMETRÍA DE CUENCA\nCRS métrico: EPSG:{epsg}\nCentroide: {ll.y():.5f}, {ll.x():.5f}\n\nGeometría\nÁrea A = {A:.3f} km²\nPerímetro P = {P:.3f} km\nLongitud de referencia L = {L:.3f} km (entrada manual; ver cauce principal calculado abajo)\n\nForma\nFactor de forma Rf = {Rf:.3f}\nRelación de elongación Re = {Re:.3f}\nRelación de circularidad Rc = {Rc:.3f}\nCoeficiente de compacidad Kc = {Kc:.3f}\n\nDrenaje\nLongitud total de corrientes = {totalL:.3f} km\nNúmero de segmentos = {Nu}\nDensidad de drenaje Dd = {Dd:.3f} km/km²\nFrecuencia de corrientes Fs = {Fs:.3f} 1/km²\nRazón de bifurcación media Rb = {Rb:.3f}\nÓrdenes Strahler = {orders}\n\nRelieve (MDE recortado a la cuenca)\nRelieve = {relief:.2f} m\nRelief ratio = {Rr:.5f}\nRuggedness number = {Rn:.3f}\nIntegral hipsométrica aprox. = {HI:.3f}\n'''
            self.out.setPlainText(txt)
            if calculate_terrain: self.start_terrain_analysis(txt)
        except Exception as e: QMessageBox.critical(self,'DROPS-Hydro MX',str(e))
    def start_terrain_analysis(self, base_report):
        if getattr(self,'_terrain_task',None) is not None: return
        dem=self.layer(self.dem)
        filled=self.filled_input.text().strip() or self.auto.get('filled_dem_path','')
        # Keep neighbor elevations around the mask for Horn derivatives. The
        # visible Gastos input is the clipped filled DEM; support uses its source.
        if self.use_basin_inputs.isChecked() and self.auto.get('filled_dem_path'):
            filled=self.auto['filled_dem_path']
        if filled:
            dem=QgsRasterLayer(filled,'filled_dem')
            if not dem.isValid(): raise ValueError('El filled DEM no es válido.')
        if not dem:
            self.out.setPlainText(base_report+'\nPendientes: seleccione un MDE con elevaciones en metros.')
            return
        basin,g,metric,ll,epsg=self.basin_geom()
        target=QgsCoordinateReferenceSystem('EPSG:%d'%epsg)
        stream=self.layer(self.streams); lines=[]
        if stream:
            transform=QgsCoordinateTransform(stream.crs(),target,QgsProject.instance())
            request=QgsFeatureRequest().setSubsetOfAttributes([])
            for feature in stream.getFeatures(request):
                geometry=feature.geometry()
                if geometry.isNull(): continue
                geometry.convertToStraightSegment(); geometry.transform(transform)
                parts=geometry.asMultiPolyline() if geometry.isMultipart() else [geometry.asPolyline()]
                lines.extend([[(p.x(),p.y()) for p in part] for part in parts if len(part)>1])
        outlet=None
        if self.outlet_stream is not None and self.outlet_crs is not None:
            point=QgsCoordinateTransform(self.outlet_crs,target,QgsProject.instance()).transform(self.outlet_stream)
            outlet=(point.x(),point.y())
        directory=self.autodir.text().strip(); os.makedirs(directory,exist_ok=True)
        path=os.path.join(directory,'cauce_principal_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.gpkg')
        task=TerrainTask(self._source_path(dem),metric.asWkt(),lines,outlet,epsg,path,
            lambda ok,report,channel,error:self._terrain_done(ok,report,channel,error,base_report),
            raster_inputs={name:path for name,path in {
                'mde_original':self.auto.get('dem_path'),
                'filled':filled,
                'acumulacion':self.auto.get('acc'),
                'direccion':self.auto.get('drainage'),
                'escurrimientos':self.auto.get('stream_raster')}.items() if path})
        task.basin_key=g.asWkt()+'|'+basin.crs().toWkt()
        self._terrain_task=task; self._start_progress('Pendientes y cauce principal')
        task.progressChanged.connect(lambda p:self._progress_update(p,'Analizando terreno y red…'))
        self.tabs.setEnabled(False); self.cancel_task_button.setEnabled(True)
        QgsApplication.taskManager().addTask(task)

    def _terrain_done(self, ok, report, channel, error, base_report):
        metrics=getattr(self._terrain_task,'metrics',{})
        metrics['basin_key']=getattr(self._terrain_task,'basin_key','')
        self._terrain_task=None
        completed=False
        try:
            if not ok: raise RuntimeError(error)
            self._terrain_metrics=metrics
            products=metrics.get('basin_rasters',{})
            full_paths={self.auto.get(key) for key in ('dem_path','analysis_dem_path','filled_dem_path','acc','drainage','stream_raster')}
            full_paths.update(self.auto.get('basin_rasters',{}).values())
            full_paths.add(self.filled_input.text().strip())
            full_paths.discard(None); full_paths.discard('')
            for existing in QgsProject.instance().mapLayers().values():
                if isinstance(existing,QgsRasterLayer) and self._source_path(existing) in full_paths:
                    node=QgsProject.instance().layerTreeRoot().findLayer(existing.id())
                    if node: node.setItemVisibilityChecked(False)
            for name,path in products.items():
                raster=QgsRasterLayer(path,'DH_'+name+'_Cuenca')
                if not raster.isValid() or not raster.crs().isValid():
                    raise ValueError('No se pudo abrir el recorte con CRS: '+path)
                QgsProject.instance().addMapLayer(raster)
                if name=='mde': self.auto['basin_dem_id']=raster.id()
            self.auto['basin_rasters']=products
            self.auto['basin_products_key']=metrics.get('basin_key','')
            selected_basin=self.basin.currentData()
            self.refresh_layers()
            index=self.basin.findData(selected_basin)
            if index>=0: self.basin.setCurrentIndex(index)
            self._save_session_state()
            self._morphometry(False)
            base_report=self.out.toPlainText()
            if 'basin_slope_pct' in metrics: self.basinSlope.setValue(metrics['basin_slope_pct'])
            if channel:
                layer=QgsVectorLayer(channel+'|layername=cauce_principal','DH_CaucePrincipal','ogr')
                if not layer.isValid(): raise RuntimeError('No se pudo abrir el cauce principal: '+channel)
                QgsProject.instance().addMapLayer(layer)
                network_path=metrics.get('network_path')
                if network_path:
                    network=QgsVectorLayer(network_path,'DH_Red_Cuenca_Strahler','ogr')
                    if not network.isValid(): raise ValueError('No se pudo abrir la red recortada.')
                    QgsProject.instance().addMapLayer(network)
                    for layer_id in (self.auto.get('streams_id'),self.auto.get('basin_streams_id')):
                        if layer_id:
                            node=QgsProject.instance().layerTreeRoot().findLayer(layer_id)
                            if node: node.setItemVisibilityChecked(False)
                    self.auto.update(basin_streams_id=network.id(),basin_streams_path=network_path)
                    selected_basin=self.basin.currentData(); selected_dem=self.dem.currentData()
                    self.stream_index=None; self.refresh_layers()
                    for combo,layer_id in ((self.basin,selected_basin),(self.dem,selected_dem)):
                        index=combo.findData(layer_id)
                        if index>=0: combo.setCurrentIndex(index)
                    self._save_session_state()
                length=metrics.get('length_m')
                if length:
                    self.Lmain.setMaximum(max(self.Lmain.maximum(),length))
                    self.Lmain.setValue(length)
                    self._morphometry(False)
                    base_report=self.out.toPlainText().replace('Longitud de referencia L =','Longitud del cauce principal L =').replace(' (entrada manual; ver cauce principal calculado abajo)','')
                report+='Capa de cauce principal: '+channel+'\n'
            self.out.setPlainText(base_report+report)
            self._sync_basin_inputs()
            self._save_session_state()
            self._finish_progress(True); completed=True
        except Exception as exc:
            self.out.setPlainText(base_report+'\nPendientes no completadas: '+str(exc))
            self._finish_progress(False)
        finally:
            self.tabs.setEnabled(True); self.cancel_task_button.setEnabled(False)
            callback=getattr(self,'_terrain_followup',None); self._terrain_followup=None
            if completed and callback:
                try: callback()
                except Exception as exc: QMessageBox.warning(self,'Parámetros automáticos',str(exc))

    def pickcsv(self):
        p,_=QFileDialog.getOpenFileName(self,'CSV de precipitación','','CSV (*.csv)'); self.csvpath.setText(p)
    def pickdir(self):
        p=QFileDialog.getExistingDirectory(self,'Carpeta de salida');
        if p: self.outdir.setText(p)
    def read_csv(self):
        try:
            p=self.csvpath.text(); annual={}
            with open(p,encoding='utf-8-sig') as f:
                r=csv.DictReader(f)
                for row in r:
                    d=datetime.date.fromisoformat(row['date']); v=float(row['precip_mm']); annual[d.year]=max(annual.get(d.year,-1),v)
            self.chirps_series=sorted(annual.items()); self.out.setPlainText('Máximos anuales cargados:\n'+'\n'.join(f'{y}: {v:.2f} mm' for y,v in self.chirps_series))
        except Exception as e: QMessageBox.critical(self,'CSV',str(e))
    def _basin_centroid_wgs84(self):
        basin=self.layer(self.basin)
        if basin is None or not basin.isValid():
            raise ValueError('Seleccione una cuenca válida en la pestaña Entradas para obtener el centroide.')
        if not basin.crs().isValid():
            raise ValueError('La cuenca no tiene un CRS válido. Defina su CRS antes de consultar CHIRPS.')
        geometry=self._union_geometry(basin)
        centroid=geometry.centroid()
        if centroid.isEmpty() or centroid.isNull():
            raise ValueError('No se pudo calcular el centroide de la cuenca.')
        transform=QgsCoordinateTransform(basin.crs(),QgsCoordinateReferenceSystem('EPSG:4326'),QgsProject.instance())
        point=transform.transform(QgsPointXY(centroid.asPoint()))
        lon,lat=point.x(),point.y()
        if not (math.isfinite(lon) and math.isfinite(lat) and -180<=lon<=180 and -90<=lat<=90):
            raise ValueError('El centroide transformado no tiene coordenadas WGS84 válidas.')
        return lon,lat

    def _update_chirps_centroid(self, *args):
        try:
            lon,lat=self._basin_centroid_wgs84()
            for widget,value in ((self.chirps_lon,lon),(self.chirps_lat,lat)):
                widget.setEnabled(True); widget.setValue(value)
                widget.setToolTip('Calculado desde la cuenca seleccionada; coordenadas WGS84.')
        except Exception as exc:
            for widget in (self.chirps_lon,self.chirps_lat):
                widget.clear(); widget.setEnabled(False); widget.setToolTip(str(exc))

    def _legacy_chirps_geometry(self):
        # Recompute immediately before every request, including geometry edits.
        lon,lat=self._basin_centroid_wgs84()
        self._update_chirps_centroid()
        h=0.025
        geom={'type':'Polygon','coordinates':[[[lon-h,lat-h],[lon+h,lat-h],[lon+h,lat+h],[lon-h,lat+h],[lon-h,lat-h]]]}
        return geom, f'área de 0.05° centrada en el centroide de cuenca: lat {lat:.6f}, lon {lon:.6f} (WGS84)'

    def _chirps_task_done(self, ok, data, error, label):
        self._chirps_task=None
        try:
            if not ok: raise RuntimeError(error or 'La solicitud ClimateSERV no terminó correctamente.')
            annual={}
            for d,v in data: annual[d.year]=max(annual.get(d.year,-1.0),v)
            outdir=self._chirps_output_dir; os.makedirs(outdir,exist_ok=True)
            path=os.path.join(outdir,'chirps_daily_climateserv.csv')
            temp=path+'.tmp'
            with open(temp,'w',newline='',encoding='utf-8') as fh:
                wr=csv.writer(fh); wr.writerow(['date','precip_mm'])
                wr.writerows((d.isoformat(),f'{v:.4f}') for d,v in data)
            os.replace(temp,path)
            self.chirps_daily=data; self.chirps_series=sorted(annual.items())
            self._rain_label=label; self._rain_basin_wkt=getattr(self,'_pending_rain_basin',None)
            self.chirps_corrected_daily=[]; self.design={}
            self._finish_progress(True)
            self.out.setPlainText(f'CHIRPS COMPLETADO — {label}\nRegistros diarios: {len(data)}\nAños completos: {len(annual)}\nCSV: {path}\n\n'+
                '\n'.join(f'{y}: {v:.2f} mm' for y,v in self.chirps_series))
        except Exception as exc:
            self._finish_progress(False)
            self.out.setPlainText('CHIRPS no completado: '+str(exc)+'\nLos bloques completos se conservan en caché para reintentar.')
            QMessageBox.warning(self,'CHIRPS',str(exc))
        finally:
            self.tabs.setEnabled(True); self.cancel_task_button.setEnabled(False)

    def read_chirps(self):
        if self.chirps_provider.currentIndex()==1:
            return self.read_chirps_v3_direct()
        try:
            if getattr(self,'_chirps_task',None) is not None:
                QMessageBox.information(self,'CHIRPS','Ya hay una descarga/proceso CHIRPS en ejecución.'); return
            folder=self._prepare_chirps_folder()
            if folder is None: return
            geom,label=self._chirps_geometry(); y0,y1=self.y0.value(),self.y1.value()
            if y1<y0: raise ValueError('El año final debe ser mayor o igual al inicial.')
            if y1>=datetime.date.today().year: raise ValueError('Seleccione años completos anteriores al año actual para analizar extremos.')
            self._start_progress('CHIRPS vía ClimateSERV')
            self.progress.setRange(0,100); self.progress.setValue(0)
            self.progress_label.setText('Enviando trabajo a ClimateSERV…')
            self._chirps_output_dir=folder
            if not self._chirps_output_dir: raise ValueError('Seleccione una carpeta de trabajo.')
            self.chirps_daily=[]; self.chirps_series=[]; self.chirps_corrected_daily=[]; self.design={}
            task=ClimateServChirpsTask(y0,y1,geom,lambda ok,data,err:self._chirps_task_done(ok,data,err,label),
                os.path.join(self._chirps_output_dir,'chirps_cache'))
            task.status.connect(lambda message:self._progress_update(None,message))
            self.tabs.setEnabled(False); self.cancel_task_button.setEnabled(True)
            task.progressChanged.connect(lambda p:self._progress_update(p,None))
            self._chirps_task=task; QgsApplication.taskManager().addTask(task)
        except Exception as e:
            self._chirps_task=None; self._finish_progress(False)
            self.tabs.setEnabled(True); self.cancel_task_button.setEnabled(False)
            QMessageBox.critical(self,'CHIRPS',str(e))

    def read_chirps_v3_direct(self):
        try:
            folder=self._prepare_chirps_folder()
            if folder is None: return
            if self.chirps_location.currentIndex()==0: raise ValueError('Para promedio de cuenca use ClimateSERV v2. CHIRPS v3 directo solo consulta el centroide.')
            geom,label=self._chirps_geometry()
            # Sample the exact centroid; averaging a closed polygon's vertices
            # would bias coordinates toward its duplicated first vertex.
            lon,lat=self._basin_centroid_wgs84(); pt=QgsPointXY(lon,lat)
            annual={}; daily=[]; y0,y1=self.y0.value(),self.y1.value(); total=(datetime.date(y1,12,31)-datetime.date(y0,1,1)).days+1; done=0
            self.out.setPlainText('Leyendo CHIRPS v3 RNL COG remoto día por día. Este modo es considerablemente más lento que ClimateSERV…')
            d=datetime.date(y0,1,1); end=datetime.date(y1,12,31)
            while d<=end:
                url=f'/vsicurl/https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/final/rnl/cogs/{d.year}/chirps-v3.0.rnl.{d:%Y.%m.%d}.cog'
                r=QgsRasterLayer(url,'chirps3','gdal')
                if r.isValid():
                    val,ok=r.dataProvider().sample(pt,1)
                    if ok and val is not None and val>-9000:
                        v=float(val); daily.append((d,v)); annual[d.year]=max(annual.get(d.year,-1),v)
                done+=1
                if done%30==0: self.out.setPlainText(f'CHIRPS v3 directo: {done}/{total} días…')
                d+=datetime.timedelta(days=1)
            from .hydro_models import export_csv
            daily_path=os.path.join(folder,'chirps_v3_diario.csv')
            export_csv(daily_path,[{'date':date.isoformat(),'precip_mm':value} for date,value in daily])
            self.chirps_daily=daily; self.chirps_series=sorted(annual.items()); self.csvpath.setText(daily_path)
            self.out.setPlainText(f'CHIRPS v3 directo completado: {len(daily)} días, {len(self.chirps_series)} años.\nCSV: {daily_path}\n'+ '\n'.join(f'{y}: {v:.2f} mm' for y,v in self.chirps_series))
        except Exception as e: QMessageBox.critical(self,'CHIRPS v3',str(e))
    def periods(self): return [float(x.strip()) for x in self.rps.text().split(',') if x.strip()]
    def _legacy_fit_returns(self):
        try:
            vals=[v for _,v in self.chirps_series]
            if len(vals)<10: raise ValueError('Se requieren al menos 10 máximos anuales.')
            mean=statistics.mean(vals); sd=statistics.stdev(vals); beta=sd*math.sqrt(6)/math.pi; mu=mean-0.5772156649*beta; self.design={}
            n=self.ddfexp.value(); d=self.duration.value()
            lines=[f'Gumbel (n={len(vals)} años): media={mean:.2f} mm, SD={sd:.2f} mm','T | P24 (mm) | P diseño (mm)']
            for T in self.periods():
                p=1-1/T; q=mu-beta*math.log(-math.log(p)); qd=q*((d/24.0)**n); self.design[T]=qd; lines.append(f'{T:g} | {q:8.2f} | {qd:12.2f}')
            lines.append('\nNota: CHIRPS es precipitación diaria. La conversión subdiaria usa el exponente DDF indicado por el usuario; para diseño final use IDF locales cuando estén disponibles.')
            self.out.setPlainText('\n'.join(lines))
        except Exception as e: QMessageBox.critical(self,'Extremos',str(e))
    def hyd(self,P,area=None,tc_value=None):
        A=self.basic()[0] if area is None else area; cn=self.cn.value(); S=25400/cn-254; Ia=.2*S; Q=0 if P<=Ia else (P-Ia)**2/(P+.8*S)
        tc=self.selected_tc() if tc_value is None else tc_value
        lag=.6*tc; tp=max(.01,self.duration.value()/2+lag); qp=.208*A*Q/tp; vol=Q/1000*A*1e6
        return Q,vol,tc,tp,qp
    def _legacy_return_flows(self):
        try:
            if not self.design: self.fit_returns()
            lines=['GASTOS DE DISEÑO','T | P (mm) | Qesc (mm) | Vol (m³) | Tc (h) | Qp (m³/s)']
            area=self.basic()[0]; tc_value=self.selected_tc()
            for T,P in sorted(self.design.items()):
                q,v,tc,tp,qp=self.hyd(P,area,tc_value); lines.append(f'{T:>4g} | {P:7.2f} | {q:9.2f} | {v:10.0f} | {tc:6.2f} | {qp:10.2f}')
            self.out.setPlainText('\n'.join(lines)+'\n\nQp: hidrograma unitario NRCS simplificado; úselo para screening/docencia y valide con hidrología local calibrada.')
        except Exception as e: QMessageBox.critical(self,'Gastos',str(e))
    def _legacy_flood(self):
        try:
            import processing
            dem=self.layer(self.dem)
            if not dem: raise ValueError('Seleccione un MDE.')
            T=float(self.floodrp.currentText())
            if T not in self.design: raise ValueError('Primero calcule las lluvias de diseño en la pestaña CHIRPS/extremos.')
            P=self.design[T]; q,vol,tc,tp,qp=self.hyd(P); intensity=q/max(self.duration.value(),.01) # effective rainfall mm/h
            out=self.outdir.text(); os.makedirs(out,exist_ok=True); depth=os.path.join(out,f'depth_T{int(T)}.tif'); discharge=os.path.join(out,f'discharge_T{int(T)}.tif')
            algs=[a.id() for a in QgsApplication.processingRegistry().algorithms() if a.id().endswith(':r.sim.water') or a.id().endswith(':r.sim.water')]
            if not algs: raise ValueError('No se encontró GRASS r.sim.water. Active el proveedor GRASS en QGIS Processing.')
            alg=algs[0]; params={'elevation':dem.source(),'rain_value':float(intensity),'infil_value':0.0,'man_value':self.manning.value(),'niterations':self.simmins.value(),'depth':depth,'discharge':discharge,'error':'TEMPORARY_OUTPUT','walkers_output':'TEMPORARY_OUTPUT','logfile':os.path.join(out,f'sim_T{int(T)}.txt')}
            res=processing.run(alg,params,feedback=QgsProcessingFeedback())
            for p,nm in [(depth,f'Profundidad T{int(T)}'),(discharge,f'Descarga T{int(T)}')]:
                if os.path.exists(p): QgsProject.instance().addMapLayer(QgsRasterLayer(p,nm))
            self.out.setPlainText(f'Mapas generados para T={T:g} años.\nP diseño={P:.2f} mm; lluvia efectiva={q:.2f} mm; intensidad efectiva={intensity:.2f} mm/h; Qp={qp:.2f} m³/s.\n\nSalidas:\n{depth}\n{discharge}\n\nLa simulación r.sim.water representa escurrimiento superficial 2D a escala de paisaje; requiere revisar resolución del MDE, Manning, infiltración y condiciones hidráulicas antes de uso ingenieril.')
        except Exception as e: QMessageBox.critical(self,'Inundación',str(e))
