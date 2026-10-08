"""UI orchestration for distributions, composite CN, automatic flows and SIMWE."""
import csv
import datetime
import hashlib
import json
import math
import os
from qgis.PyQt.QtWidgets import QWidget,QFormLayout,QLineEdit,QPushButton,QHBoxLayout,QLabel,QComboBox,QDoubleSpinBox,QCheckBox,QMessageBox,QFileDialog
from qgis.core import QgsApplication,QgsRasterLayer,QgsVectorLayer,QgsProject,QgsCoordinateTransform,QgsCoordinateReferenceSystem,QgsGeometry
from .hydro_models import export_csv,fit_distributions,tc_all,flow_comparison
from .spatial_jobs import FunctionTask,cn_job
from .flood_job import simulate_flood


class AdvancedHydrology:
    def _folder_row(self,layout,label,widget,first=False):
        button=QPushButton('Elegir carpeta'); row=QHBoxLayout(); row.addWidget(widget); row.addWidget(button)
        def choose():
            value=QFileDialog.getExistingDirectory(self,label,widget.text())
            if value:
                widget.setText(value); self._save_session_state()
        button.clicked.connect(choose)
        if first: layout.insertRow(0,label,row)
        else: layout.addRow(label,row)

    def _file_row(self,layout,label,widget,filter_='Raster (*.tif *.tiff *.vrt);;Todos (*)'):
        row=QHBoxLayout(); button=QPushButton('Examinar'); row.addWidget(widget); row.addWidget(button)
        def choose():
            path,_=QFileDialog.getOpenFileName(self,label,'',filter_)
            if path: widget.setText(path)
        button.clicked.connect(choose); layout.addRow(label,row)
        return button

    def _tab_rain(self):
        self._legacy_tab_rain()
        form=self.tabs.widget(self.tabs.count()-1).layout()
        self.chirps_location.clear(); self.chirps_location.addItems(['Promedio espacial de toda la cuenca','Área alrededor del centroide automático'])
        self.chirps_output=QLineEdit(os.path.join(self.autodir.text(),'CHIRPS'))
        self._folder_row(form,'Carpeta de descarga CHIRPS:',self.chirps_output,first=True)
        form.insertRow(1,QLabel('Aquí se guardan la lluvia diaria CSV, la caché de descarga y los ajustes estadísticos.'))
        self.distribution=QComboBox(); self.distribution.addItems(['Mejor AICc','Gumbel','GEV','Lognormal','Log-Pearson III'])
        self.distribution.currentIndexChanged.connect(self._distribution_changed)
        form.addRow('Distribución adoptada:',self.distribution)
        button=QPushButton('Exportar comparación y lluvias a CSV'); button.clicked.connect(self.export_frequency); form.addRow(button)
        form.addRow(QLabel('Se comparan cuatro distribuciones por AICc; KS se informa como estadístico descriptivo. La mejor es relativa a estos candidatos.'))
        for button in self.tabs.widget(self.tabs.count()-1).findChildren(QPushButton):
            if button.text().startswith('Ajustar Gumbel'): button.setText('Ajustar y comparar Gumbel, GEV, Lognormal y Log-Pearson III')

    def _tab_hydro(self):
        widget=QWidget(); form=QFormLayout(widget)
        self.cn=QDoubleSpinBox(); self.cn.setRange(0,100); self.cn.setDecimals(2); self.cn.setReadOnly(True)
        self.basinSlope=QDoubleSpinBox(); self.basinSlope.setRange(0,20000); self.basinSlope.setDecimals(3); self.basinSlope.setSuffix(' %'); self.basinSlope.setReadOnly(True)
        self.tc=QDoubleSpinBox(); self.tc.setRange(0,10000); self.tc.setReadOnly(True)
        self.autotc=QComboBox(); self.autotc.addItems(['Kirpich','Giandotti','Témez','NRCS/SCS lag','Promedio de métodos'])
        self.use_basin_inputs=QCheckBox('Usar automáticamente los archivos recortados de la cuenca'); self.use_basin_inputs.setChecked(True)
        form.addRow(self.use_basin_inputs)
        self.filled_input=QLineEdit(); self.filled_input.setPlaceholderText('Se selecciona al delimitar la cuenca')
        self.filled_browse=self._file_row(form,'Filled DEM de cuenca:',self.filled_input)
        self.basin_files=QLabel('Delimite una cuenca para seleccionar sus archivos.'); self.basin_files.setWordWrap(True)
        form.addRow('Archivos de cuenca activos:',self.basin_files)
        self.use_basin_inputs.toggled.connect(self._sync_basin_inputs)
        self.tabs.currentChanged.connect(lambda index:self._sync_basin_inputs() if index==4 else None)
        self.worldcover=QCheckBox('Leer WorldCover 2021 remoto para esta cuenca'); self.worldcover.setChecked(True)
        form.addRow(self.worldcover)
        self.cover_input=QLineEdit(); self._file_row(form,'WorldCover local (alternativa):',self.cover_input)
        self.soil_input=QLineEdit(); self.soil_input.setPlaceholderText('Vacío: obtener HYSOGs250m automáticamente para la cuenca')
        self._file_row(form,'Suelo local (opcional):',self.soil_input)
        form.addRow(QLabel('CN automático: WorldCover + HYSOGs250m (suelo de ~250 m). No necesita cargar un grupo hidrológico.'))
        self.cn_sources=QLabel('Los recortes de cobertura y suelo se guardarán junto al CN.'); self.cn_sources.setWordWrap(True)
        form.addRow(self.cn_sources)
        source_label=QLabel('<a href="https://daac.ornl.gov/SOILS/guides/Global_Hydrologic_Soil_Group.html">Información y acceso a HYSOGs250m (suelo)</a>')
        source_label.setOpenExternalLinks(True); form.addRow(source_label)
        self.cn_table_input=QLineEdit(); self.cn_table_input.setPlaceholderText('Opcional: tabla class,description,A,B,C,D')
        self._file_row(form,'Tabla CN personalizada:',self.cn_table_input,'CSV (*.csv)')
        self.cn_resolution=QDoubleSpinBox(); self.cn_resolution.setRange(10,1000); self.cn_resolution.setValue(30); self.cn_resolution.setSuffix(' m')
        form.addRow('Resolución CN:',self.cn_resolution)
        form.addRow(QLabel('Suelos: 1=A, 2=B, 3=C, 4=D; HYSOGs 11–14 se consideran no drenados (D). CN es una estimación AMC-II; revise la tabla de equivalencias.'))
        button=QPushButton('Calcular CN medio de la cuenca'); button.clicked.connect(self.calculate_cn); form.addRow(button)
        button=QPushButton('Guardar tabla CN base para revisar / editar'); button.clicked.connect(self.export_cn_table); form.addRow(button)
        form.addRow('CN ponderado por área:',self.cn); form.addRow('Pendiente media filled DEM:',self.basinSlope)
        form.addRow('Tc adoptado para consulta:',self.autotc)
        self.runoff_c=QDoubleSpinBox(); self.runoff_c.setRange(.01,1); self.runoff_c.setDecimals(3); self.runoff_c.setValue(.4)
        form.addRow('Coeficiente C (racional / HU):',self.runoff_c)
        form.addRow(QLabel('C es una hipótesis independiente del CN; no se deduce automáticamente de él.'))
        self.hydro_output=QLineEdit(os.path.join(self.autodir.text(),'Hidrologia')); self._folder_row(form,'Guardar gastos e hidrogramas:',self.hydro_output)
        button=QPushButton('Calcular automáticamente todos los Tc'); button.clicked.connect(self.compare_tc); form.addRow(button)
        button=QPushButton('Calcular todos los gastos y periodos'); button.clicked.connect(self.return_flows); form.addRow(button)
        button=QPushButton('Exportar resultados e hidrogramas CSV'); button.clicked.connect(self.export_flows); form.addRow(button)
        self.tabs.addTab(widget,'5. Gastos / Tc')
        self._sync_basin_inputs()

    def _tab_flood(self):
        self._legacy_tab_flood()
        form=self.tabs.widget(self.tabs.count()-1).layout()
        form.insertRow(0,QLabel('Profundidad: metros de agua sobre el terreno al final de la simulación. Ejemplo: 0.25 m = 25 cm.'))
        self.manning_mode=QComboBox(); self.manning_mode.addItems(['Variable por cobertura (estimación)','Raster Manning del usuario','Uniforme'])
        form.addRow('Rugosidad Manning:',self.manning_mode)
        self.manning_raster=QLineEdit(); self._file_row(form,'Raster n (si usa raster propio):',self.manning_raster)
        self.manning_table=QLineEdit(); self.manning_table.setPlaceholderText('Opcional: CSV con class,description,n; sustituye la tabla inicial')
        self._file_row(form,'Tabla Manning por cobertura:',self.manning_table,'CSV (*.csv)')
        button=QPushButton('Exportar tabla Manning inicial para revisar'); button.clicked.connect(self.export_manning_table); form.addRow(button)
        button=QPushButton('Abrir guía de inundación y unidades'); button.clicked.connect(self.open_flood_guide); form.insertRow(1,button)
        self.manning.setEnabled(False)
        self.manning_mode.currentIndexChanged.connect(lambda index:self.manning.setEnabled(index==2))
        form.addRow(QLabel('El raster n se genera sobre la grilla de simulación y modifica el cálculo de profundidad. La tabla inicial es orientativa y editable; no está calibrada.'))
        self.simmins.setEnabled(False); self.simmins.setToolTip('Se toma de la duración de lluvia en CHIRPS, para mantener el volumen de lluvia excedente.')
        self.flood_resolution=QDoubleSpinBox(); self.flood_resolution.setRange(5,1000); self.flood_resolution.setValue(30); self.flood_resolution.setSuffix(' m')
        form.addRow('Resolución simulación:',self.flood_resolution)
        form.addRow(QLabel('SIMWE: escurrimiento superficial por lluvia uniforme sobre la cuenca. Incluye MDE, dx/dy, profundidad, descarga, error y registro. No es una simulación de desbordamiento fluvial con secciones.'))

    def open_flood_guide(self):
        from qgis.PyQt.QtGui import QDesktopServices
        from qgis.PyQt.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.join(os.path.dirname(__file__),'Guia_inundacion.html')))

    def export_manning_table(self):
        from .flood_job import MANNING_TABLE
        path,_=QFileDialog.getSaveFileName(self,'Guardar tabla Manning','tabla_manning.csv','CSV (*.csv)')
        if path:
            try: export_csv(path,[{'class':code,'description':row[0],'n':row[1]} for code,row in MANNING_TABLE.items()])
            except Exception as exc: QMessageBox.warning(self,'Tabla Manning',str(exc))

    def _filled_path(self):
        self._sync_basin_inputs()
        path=self.filled_input.text().strip() or self.auto.get('filled_dem_path','')
        if not path or not os.path.isfile(path):
            raise ValueError('Genere el filled DEM en el paso 3 o selecciónelo en Gastos / Tc. No se sustituirá por un MDE sin rellenar.')
        return path

    def _sync_basin_inputs(self,*args):
        if not hasattr(self,'use_basin_inputs'): return
        automatic=self.use_basin_inputs.isChecked()
        self.filled_input.setReadOnly(automatic); self.filled_browse.setEnabled(not automatic)
        if not automatic: return
        current=self.basin.currentData() if hasattr(self,'basin') else None
        matches=current==self.auto.get('basin_id')
        if self.auto.get('basin_products_key') and current:
            basin=self.layer(self.basin)
            matches=self._union_geometry(basin).asWkt()+'|'+basin.crs().toWkt()==self.auto['basin_products_key']
        products=self.auto.get('basin_rasters',{}) if matches else {}
        filled=products.get('filled','')
        self.filled_input.setText(filled if filled and os.path.isfile(filled) else '')
        paths=[label+': '+products[name] for name,label in (
            ('mde','MDE'),('filled','Filled DEM'),('pendiente','Pendiente'),
            ('acumulacion','Acumulación'),('direccion','Dirección'),('escurrimientos','Escurrimientos'))
            if products.get(name) and os.path.isfile(products[name])]
        if matches and self.auto.get('basin_streams_id'):
            index=self.streams.findData(self.auto['basin_streams_id'])
            if index>=0: self.streams.setCurrentIndex(index)
            paths.append('Red: '+self.auto.get('basin_streams_path',''))
        self.basin_files.setText('\n'.join(paths) or 'Los recortes se generarán al calcular el terreno de la cuenca seleccionada.')

    def _prepare_chirps_folder(self):
        folder=self.chirps_output.text().strip()
        if not folder:
            folder=QFileDialog.getExistingDirectory(self,'Carpeta de descarga CHIRPS',self.autodir.text())
            if not folder: return None
            self.chirps_output.setText(folder)
        os.makedirs(folder,exist_ok=True)
        import tempfile
        with tempfile.TemporaryFile(dir=folder): pass
        self._save_session_state()
        return folder

    def _spatial_snapshot(self):
        basin,g,metric,ll,epsg=self.basin_geom()
        geographic=QgsGeometry(g); geographic.transform(QgsCoordinateTransform(basin.crs(),QgsCoordinateReferenceSystem('EPSG:4326'),QgsProject.instance()))
        box=geographic.boundingBox()
        return metric.asWkt(),epsg,(box.xMinimum(),box.yMinimum(),box.xMaximum(),box.yMaximum())

    def _submit_job(self,name,function,callback):
        if getattr(self,'_extra_task',None) is not None: raise ValueError('Espere a que termine la tarea actual.')
        def done(ok,result,error):
            self._extra_task=None; self.tabs.setEnabled(True); self.cancel_task_button.setEnabled(False)
            self._finish_progress(ok)
            if not ok:
                self._after_fit=None
                self.out.setPlainText(name+' no completado:\n'+error)
                QMessageBox.warning(self,name,error); return
            try: callback(result)
            except Exception as exc:
                self._after_fit=None; self.out.setPlainText(name+': '+str(exc)); QMessageBox.warning(self,name,str(exc))
        task=FunctionTask(name,function,done)
        task.status.connect(lambda text:self._progress_update(None,text))
        task.progressChanged.connect(lambda value:self._progress_update(value,None))
        self._extra_task=task; self._start_progress(name)
        self.tabs.setEnabled(False); self.cancel_task_button.setEnabled(True)
        QgsApplication.taskManager().addTask(task)

    def _frequency_signature(self):
        return (tuple(self.chirps_series),tuple(self.periods()))

    def fit_returns(self):
        try:
            values=[v for _,v in self.chirps_series]; periods=self.periods(); signature=self._frequency_signature()
            def calculate(task):
                task.status.emit('Ajustando cuatro distribuciones por máxima verosimilitud…')
                return fit_distributions(values,periods)
            def finished(result):
                self._fit_rows,self._quantile_rows,self._best_distribution=result; self._fit_key=signature
                self._adopt_distribution()
                self._save_frequency(self.chirps_output.text().strip())
                callback=getattr(self,'_after_fit',None); self._after_fit=None
                if callback: callback()
            self._submit_job('Distribuciones de extremos',calculate,finished)
        except Exception as exc: self._after_fit=None; QMessageBox.warning(self,'Extremos',str(exc))

    def _adopt_distribution(self):
        selected=self.distribution.currentText()
        if selected=='Mejor AICc': selected=self._best_distribution
        quantiles=[r for r in self._quantile_rows if r['distribution']==selected]
        if not quantiles: raise ValueError('La distribución seleccionada no obtuvo un ajuste válido; revise la comparación.')
        self._design24={r['T_years']:r['P24_mm'] for r in quantiles}
        self.design={t:p*(self.duration.value()/24)**self.ddfexp.value() for t,p in self._design24.items()}
        self._selected_distribution=selected
        previous=self.floodrp.currentText()
        self.floodrp.clear(); self.floodrp.addItems([f'{t:g}' for t in sorted(self._design24)])
        index=self.floodrp.findText(previous)
        if index>=0: self.floodrp.setCurrentIndex(index)
        lines=['COMPARACIÓN DE DISTRIBUCIONES','Mejor entre candidatos por AICc: '+self._best_distribution,'Adoptada: '+selected,
            'Distribución | AICc | BIC | KS D']
        for row in self._fit_rows:
            lines.append(f"{row['distribution']} | {row['AICc']:.3f} | {row['BIC']:.3f} | {row['KS_D']:.3f}" if row['status']=='OK' else row['distribution']+': '+row['status'])
        lines.extend(['','T (años) | P24 (mm) | P duración (mm)']+[f'{t:g} | {p:.3f} | {self.design[t]:.3f}' for t,p in self._design24.items()])
        lines.append('\nKS es descriptivo: los parámetros se estimaron con estos mismos datos. AICc no prueba validez de las colas. DDF subdiaria requiere validación con IDF local.')
        self.out.setPlainText('\n'.join(lines))

    def _distribution_changed(self,*args):
        if not hasattr(self,'_fit_rows'): return
        try: self._adopt_distribution()
        except Exception as exc: self.design={}; self._design24={}; self.out.setPlainText(str(exc))

    def _save_frequency(self,directory):
        if not directory: raise ValueError('Seleccione una carpeta de salida para CHIRPS.')
        rows=[]
        for row in self._quantile_rows:
            rows.append(dict(row,Pduration_mm=row['P24_mm']*(self.duration.value()/24)**self.ddfexp.value(),
                duration_h=self.duration.value(),DDF_exponent=self.ddfexp.value(),adopted=row['distribution']==self._selected_distribution,
                rainfall_scope=getattr(self,'_rain_label','CSV / fuente del usuario')))
        export_csv(os.path.join(directory,'comparacion_distribuciones.csv'),self._fit_rows)
        export_csv(os.path.join(directory,'lluvias_diseno.csv'),rows)
        export_csv(os.path.join(directory,'maximos_anuales.csv'),[dict(year=y,precip_mm=v) for y,v in self.chirps_series])

    def export_frequency(self):
        try:
            if self._frequency_signature()!=getattr(self,'_fit_key',None): raise ValueError('Ajuste las distribuciones con la serie y periodos actuales antes de exportar.')
            self._adopt_distribution()
            directory=QFileDialog.getExistingDirectory(self,'Exportar distribuciones',self.chirps_output.text())
            if directory: self._save_frequency(directory); self.out.append('\nCSV exportados a '+directory)
        except Exception as exc: QMessageBox.warning(self,'Exportar',str(exc))

    def _chirps_geometry(self):
        self._pending_rain_basin=self._union_geometry(self.layer(self.basin)).asWkt() if self.layer(self.basin) else None
        if self.chirps_location.currentIndex()==1: return self._legacy_chirps_geometry()
        basin=self.layer(self.basin)
        if not basin: raise ValueError('Seleccione una cuenca en Entradas.')
        geometry=self._union_geometry(basin)
        geometry.transform(QgsCoordinateTransform(basin.crs(),QgsCoordinateReferenceSystem('EPSG:4326'),QgsProject.instance()))
        self._update_chirps_centroid()
        if not geometry.isGeosValid(): raise ValueError('La geometría de cuenca no es válida para CHIRPS.')
        return json.loads(geometry.asJson()),'promedio espacial de toda la cuenca'

    def _calculate_cn(self,callback):
        wkt,epsg,bbox=self._spatial_snapshot(); soil=self.soil_input.text().strip(); cover=self.cover_input.text().strip()
        world=self.worldcover.isChecked(); table=self.cn_table_input.text().strip(); resolution=self.cn_resolution.value()
        key=(wkt,epsg,soil,cover,world,table,resolution,self.hydro_output.text().strip(),tuple(os.path.getmtime(p) if p and os.path.isfile(p) else None for p in (soil,cover,table)))
        if key==getattr(self,'_cn_key',None) and os.path.exists(self._cn_result['raster']): callback(self._cn_result); return
        directory=os.path.join(self.hydro_output.text().strip(),'CN_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        def done(result):
            self._cn_key=key; self._cn_result=result; self.cn.setValue(result['mean'])
            layer=QgsRasterLayer(result['raster'],'DH_CN_Cuenca')
            if layer.isValid(): QgsProject.instance().addMapLayer(layer)
            self.cn_sources.setText('Suelo de cuenca: '+result['soil_raster']+'\nCobertura de cuenca: '+result['cover_raster'])
            callback(result)
        self._submit_job('CN medio de cuenca',lambda task:cn_job(task,wkt,epsg,soil,cover,world,bbox,directory,resolution,table),done)

    def calculate_cn(self):
        try:
            self._calculate_cn(lambda result:self.out.setPlainText(f"CN medio AMC-II = {result['mean']:.2f}\nCobertura válida: {result['coverage']:.1%}\nResolución de cálculo: {result['resolution']:.1f} m\nRaster: {result['raster']}\nSuelo: {'HYSOGs automático (~250 m)' if result.get('soil_automatic') else 'capa local'}\nLa tabla CN es una hipótesis por cobertura y grupo de suelo; revise cn_crosswalk.csv, cn_areas.csv y fuentes_CN.json."))
        except Exception as exc: QMessageBox.warning(self,'CN',str(exc))

    def export_cn_table(self):
        from .spatial_jobs import CN_TABLE
        try:
            path,_=QFileDialog.getSaveFileName(self,'Guardar tabla CN base','cn_crosswalk.csv','CSV (*.csv)')
            if path:
                export_csv(path,[{'class':code,'description':row[0],'A':row[1],'B':row[2],'C':row[3],'D':row[4]} for code,row in CN_TABLE.items()])
        except Exception as exc: QMessageBox.warning(self,'Tabla CN',str(exc))

    def _start_auto_inputs(self,action):
        self._filled_path()
        expected=getattr(self,'_rain_basin_wkt',None)
        if action in ('flows','flood') and expected and expected!=self._union_geometry(self.layer(self.basin)).asWkt():
            raise ValueError('La cuenca cambió desde la descarga CHIRPS. Descargue la lluvia para la cuenca actual.')
        if self.layer(self.streams) is None or self.outlet_stream is None:
            raise ValueError('Seleccione la red y el outlet para calcular automáticamente longitud y pendientes del cauce.')
        self._terrain_followup=lambda:self._calculate_cn(lambda result:self._finish_hydro_action(action))
        try: self.start_terrain_analysis('Preparando parámetros automáticos con el filled DEM…\n')
        except Exception: self._terrain_followup=None; raise

    def _geom_hydro_params(self):
        m=getattr(self,'_terrain_metrics',{})
        if m.get('network_unordered',0):
            raise ValueError('La red contiene tramos sin orden por ciclos o desconexión. Corrija su topología antes de calcular gastos automáticos.')
        if not all(key in m for key in ('area_km2','length_m','channel_slope','z_mean','z_out')):
            raise ValueError('Calcule los parámetros automáticos con el filled DEM.')
        if m.get('channel_slope') is None or m.get('z_out') is None: raise ValueError('No hay cotas válidas del cauce principal.')
        return m['area_km2'],m['length_m']/1000,m['channel_slope'],m['z_mean']-m['z_out']

    def tc_methods(self):
        area,L,slope,height=self._geom_hydro_params()
        return tc_all(area,L*1000,slope,self._terrain_metrics['basin_slope_pct'],height,self.cn.value())

    def compare_tc(self):
        try: self._start_auto_inputs('tc')
        except Exception as exc: QMessageBox.warning(self,'Tc automático',str(exc))

    def return_flows(self):
        try:
            if self._frequency_signature()!=getattr(self,'_fit_key',None):
                self._after_fit=self.return_flows; self.fit_returns(); return
            self._adopt_distribution(); self._start_auto_inputs('flows')
        except Exception as exc: QMessageBox.warning(self,'Gastos',str(exc))

    def _finish_hydro_action(self,action):
        times=self.tc_methods(); metrics=self._terrain_metrics
        self.basinSlope.setValue(metrics['basin_slope_pct']); self.tc.setValue(times[self.autotc.currentText()])
        self._tc_rows=[dict(method=method,Tc_h=value,CN_mean=self.cn.value(),area_km2=metrics['area_km2'],
            length_m=metrics['length_m'],channel_slope=metrics['channel_slope'],basin_slope_pct=metrics['basin_slope_pct'],
            mean_height_above_outlet_m=metrics['z_mean']-metrics['z_out'],filled_dem=metrics['dem_path']) for method,value in times.items()]
        directory=self.hydro_output.text().strip()
        export_csv(os.path.join(directory,'tiempos_concentracion.csv'),self._tc_rows)
        if action=='tc':
            self.out.setPlainText('TIEMPOS AUTOMÁTICOS\n'+'\n'.join(f'{k}: {v:.3f} h' for k,v in times.items())+'\nCSV: '+directory); return
        if action=='flood': self._run_flood_ready(); return
        self._flow_rows,self._hydrograph_rows=flow_comparison(self._design24,metrics['area_km2'],self.cn.value(),times,
            self.duration.value(),self.ddfexp.value(),self.runoff_c.value())
        for row in self._flow_rows:
            row.update(distribution=self._selected_distribution,rainfall_scope=getattr(self,'_rain_label','CSV del usuario'),
                basin_slope_pct=metrics['basin_slope_pct'],filled_dem=metrics['dem_path'])
        self._save_flows(directory)
        lines=['GASTOS POR MÉTODO Y PERIODO','CN medio = %.2f; pendiente media filled DEM = %.3f %%'%(self.cn.value(),metrics['basin_slope_pct'])]
        lines.extend(f"T={r['T_years']:g} | {r['Tc_method']} | {r['runoff_method']} | Qp={r.get('Qpeak_m3s',float('nan')):.3f} m³/s" for r in self._flow_rows)
        lines.append('\nCSV de gastos, Tc e hidrogramas: '+directory+'\nLos métodos empíricos tienen distintos dominios de aplicación; el promedio de Tc es una comparación, no una calibración.')
        self.out.setPlainText('\n'.join(lines))

    def _save_flows(self,directory):
        export_csv(os.path.join(directory,'tiempos_concentracion.csv'),self._tc_rows)
        export_csv(os.path.join(directory,'gastos_metodos.csv'),self._flow_rows)
        export_csv(os.path.join(directory,'hidrogramas.csv'),self._hydrograph_rows)

    def export_flows(self):
        try:
            if not getattr(self,'_flow_rows',None): raise ValueError('Calcule los gastos antes de exportar.')
            directory=QFileDialog.getExistingDirectory(self,'Exportar gastos',self.hydro_output.text())
            if directory: self._save_flows(directory); self.out.append('\nExportado a '+directory)
        except Exception as exc: QMessageBox.warning(self,'Exportar',str(exc))

    def flood(self):
        try:
            if self._frequency_signature()!=getattr(self,'_fit_key',None):
                self._after_fit=self.flood; self.fit_returns(); return
            self._adopt_distribution(); self._start_auto_inputs('flood')
        except Exception as exc: QMessageBox.warning(self,'Inundación',str(exc))

    def _run_flood_ready(self):
        algorithm=self._alg('r.sim.water')
        if not algorithm: raise ValueError('Active el proveedor GRASS de Processing: falta r.sim.water.')
        import processing
        wkt,epsg,_=self._spatial_snapshot(); period=float(self.floodrp.currentText())
        path=self._filled_path(); duration=self.duration.value(); rain=self.design[period]
        # SIMWE also needs a one-cell support ring for spatial derivatives;
        # its outputs remain masked to the selected basin.
        if self.use_basin_inputs.isChecked() and os.path.isfile(self.auto.get('filled_dem_path','')):
            path=self.auto['filled_dem_path']
        self.simmins.setValue(round(duration*60))
        directory=os.path.join(self.outdir.text().strip(),'T'+f'{period:g}'+'_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        params=dict(T_years=period,rain_mm=rain,CN_mean=self.cn.value(),duration_h=duration,manning=self.manning.value(),
            distribution=self._selected_distribution,rainfall_scope=getattr(self,'_rain_label','CSV del usuario'))
        params.update(manning_mode=('cover','raster','constant')[self.manning_mode.currentIndex()],
            manning_raster=self.manning_raster.text().strip(),manning_table=self.manning_table.text().strip(),
            manning_cover=self._cn_result['cover_raster'])
        resolution=self.flood_resolution.value()
        def done(result):
            for key,name in [('depth','DH_Profundidad_m'),('discharge','DH_DescargaSuperficial_m3s'),('error','DH_ErrorSimulacion_m'),('manning','DH_Manning_n')]:
                layer=QgsRasterLayer(result['outputs'][key],name+'_T'+f'{period:g}')
                if not layer.isValid(): raise ValueError('No se pudo abrir '+result['outputs'][key])
                if key=='depth':
                    from qgis.core import QgsColorRampShader,QgsRasterShader,QgsSingleBandPseudoColorRenderer
                    from qgis.PyQt.QtGui import QColor
                    ramp=QgsColorRampShader(); ramp.setColorRampType(QgsColorRampShader.Interpolated)
                    maximum=max(5.01,result['max_depth_m'])
                    ramp.setColorRampItemList([QgsColorRampShader.ColorRampItem(value,QColor(color),label) for value,color,label in
                        [(0,'#00ffffff','Seco'),(.01,'#deebf7','0.01 m'),(.5,'#9ecae1','0.5 m'),(1,'#4292c6','1 m'),(2,'#2171b5','2 m'),(5,'#084594','5 m'),(maximum,'#041f4a',f'{maximum:.2f} m')]])
                    shader=QgsRasterShader(); shader.setRasterShaderFunction(ramp)
                    layer.setRenderer(QgsSingleBandPseudoColorRenderer(layer.dataProvider(),1,shader))
                    layer.saveNamedStyle(os.path.join(directory,'profundidad.qml'))
                QgsProject.instance().addMapLayer(layer)
            self.out.setPlainText(f"SIMWE TERMINÓ\nT={period:g} años; duración {result['simulation_minutes']} min\nProfundidad en metros al final de la simulación (no máximo temporal).\nManning: {result['roughness']['mode']}, n = {result['roughness']['min']:.3f} a {result['roughness']['max']:.3f}\nResolución: {result['resolution_m']:.2f} m\nLluvia excedente: {result['rainfall_excess_mm']:.3f} mm\nControl de volumen: {result['volume_QA']}\nSalidas, guía y Como_se_calculo.html: {directory}\nModelo de escurrimiento superficial por lluvia; no representa por sí solo desbordamiento fluvial.")
        self._submit_job('Inundación SIMWE',lambda task:simulate_flood(task,path,wkt,epsg,directory,resolution,params,algorithm),done)
