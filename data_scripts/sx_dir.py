import sys
import re

class Edge:
 
    def __init__(self, u, v, w):
        self.u = u
        self.v = v
        self.w = w
 
    def __repr__(self):
        return '{' + str(self.u) + ', ' + str(self.v) + ', ' + str(self.w) + '}'

# reader = open('data/netflow_sample_comb.txt', 'r')
# writer = open('data/netflow_sample_comb_unique.txt', 'w')

reader = open('/mnt/cci-files/stackoverflow_temporal/sx-stackoverflow.txt', 'r')
writer = open('/mnt/cci-files/stackoverflow_temporal/sx-unique-dir.txt', 'w')

min_vtx = sys.maxsize
max_vtx = -sys.maxsize - 1
edges = []
unique_edges = []
last_u = -1
last_v = -1

try:
    for line in reader.readlines():
        words = line.split(' ')
        src = int(words[0])
        dst = int(words[1])
        weight = int(words[2])
        edges.append(Edge(src, dst, weight))
        
        min_vtx = min(min_vtx, src)
        min_vtx = min(min_vtx, dst)
        
        max_vtx = max(max_vtx, src)
        max_vtx = max(max_vtx, dst)
    
    edges.sort(key=lambda x: (x.u, x.v, x.w))
    # print("edges: {}".format(edges))
    
    for e in edges:
        if e.u == last_u and e.v == last_v:
            continue;
        else:
            unique_edges.append(e)
            last_u = e.u
            last_v = e.v
    
    unique_edges.sort(key=lambda x: x.w)
    
    for e in unique_edges:
        wline = str(e.u) + ' ' + str(e.v) + ' ' + str(e.w) + '\n'
        writer.write(wline)
finally:
    print("Max Vertex-id: {}, Min Vertex-id: {}".format(max_vtx, min_vtx))
    reader.close()
    writer.close()