"""Topological Strahler ordering of a dendritic network rooted at its outlet."""
import heapq
import math
from qgis.core import QgsGeometry,QgsPointXY


def order_network(lines,outlet,tolerance,check=lambda:None):
    merged=QgsGeometry.unaryUnion([QgsGeometry.fromPolylineXY([QgsPointXY(*p) for p in line]) for line in lines if len(line)>1])
    if merged.isEmpty(): raise ValueError('Red vacía al recortar la cuenca.')
    parts=merged.asMultiPolyline() if merged.isMultipart() else [merged.asPolyline()]
    edges=[]
    for part in parts:
        for a,b in zip(part,part[1:]):
            a=(a.x(),a.y()); b=(b.x(),b.y())
            if a!=b: edges.append((a,b))
    best=None
    for i,(a,b) in enumerate(edges):
        check(); dx,dy=b[0]-a[0],b[1]-a[1]
        t=max(0,min(1,((outlet[0]-a[0])*dx+(outlet[1]-a[1])*dy)/(dx*dx+dy*dy)))
        p=(a[0]+t*dx,a[1]+t*dy); distance=math.dist(p,outlet)
        if best is None or distance<best[0]: best=distance,i,p
    if best is None or best[0]>tolerance: raise ValueError('El outlet no coincide con la red recortada.')
    _,i,root=best; a,b=edges.pop(i)
    if math.dist(root,a)<1e-7: root=a
    if math.dist(root,b)<1e-7: root=b
    edges.extend([(a,root),(root,b)])
    graph={}
    for a,b in edges:
        if a==b: continue
        graph.setdefault(a,{})[b]=math.dist(a,b); graph.setdefault(b,{})[a]=math.dist(a,b)
    distances={root:0.}; parent={}; queue=[(0.,root)]
    while queue:
        check(); distance,node=heapq.heappop(queue)
        if distance!=distances[node]: continue
        for nxt,length in graph[node].items():
            if distance+length<distances.get(nxt,float('inf')):
                distances[nxt]=distance+length; parent[nxt]=node; heapq.heappush(queue,(distance+length,nxt))
    edge_count=sum(sum(n in distances for n in graph[v]) for v in distances)//2
    if edge_count!=len(distances)-1:
        raise ValueError('Red con ciclos o bifurcaciones: Strahler requiere una red dendrítica. Revise la topología.')
    children={node:[] for node in distances}
    for child,downstream in parent.items(): children[downstream].append(child)
    orders={}
    for node in sorted(distances,key=distances.get,reverse=True):
        incoming=[orders[c] for c in children[node]]
        high=max(incoming,default=1)
        orders[node]=high+1 if incoming.count(high)>=2 else high
    reaches=[]
    # Collapse degree-2 vertices so counts describe reaches, not raster pixels.
    for node in distances:
        if node==root or len(children[node])==1: continue
        route=[node]; current=node
        while current!=root:
            current=parent[current]; route.append(current)
            if current==root or len(children[current])!=1: break
        if len(route)>1: reaches.append((route,orders[node],'connected'))
    visited=set()
    for a in graph:
        if a in distances: continue
        for b in graph[a]:
            key=frozenset((a,b))
            if key not in visited:
                reaches.append(([a,b],None,'disconnected')); visited.add(key)
    return reaches


def append_network(path,lines,outlet,tolerance,epsg,check=lambda:None):
    from osgeo import ogr,osr
    try: reaches=order_network(lines,outlet,tolerance,check)
    except ValueError as exc:
        if 'ciclos' not in str(exc): raise
        # Preserve the clipped product even when no defensible order exists.
        reaches=[(line,None,'cycle') for line in lines if len(line)>1]
    ds=ogr.Open(path,1)
    srs=osr.SpatialReference(); srs.ImportFromEPSG(epsg)
    layer=ds.CreateLayer('red_cuenca',srs,ogr.wkbLineString)
    for name,type_ in [('Strahler',ogr.OFTInteger),('length_m',ogr.OFTReal),('topology',ogr.OFTString)]:
        layer.CreateField(ogr.FieldDefn(name,type_))
    for points,order,status in reaches:
        check(); feature=ogr.Feature(layer.GetLayerDefn()); geometry=ogr.Geometry(ogr.wkbLineString)
        for x,y in points: geometry.AddPoint_2D(x,y)
        feature.SetGeometry(geometry); feature.SetField('length_m',geometry.Length()); feature.SetField('topology',status)
        if order is not None: feature.SetField('Strahler',order)
        if layer.CreateFeature(feature)!=0: raise ValueError('No se pudo guardar la red ordenada.')
    feature=None; layer=None; ds=None
    return len(reaches),sum(order is None for _,order,_ in reaches)
