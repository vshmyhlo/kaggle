import pandas as pd
from ase import Atoms

# import ase.visualize
# from mol.xyz2mol import xyz2mol, read_xyz_file

filename = './data/mol/structures/dsgdb9nsd_133882.xyz'

struct_file = pd.read_csv('./data/mol/structures.csv')
molecule = struct_file[struct_file['molecule_name'] == 'dsgdb9nsd_133882']
atoms = molecule.iloc[:, 3:].values
symbols = molecule.iloc[:, 2].values

system = Atoms(positions=atoms, symbols=symbols)

print(system)

# charged_fragments = True
# quick = True
# atomicNumList, charge, xyz_coordinates = read_xyz_file(filename)
# mol = xyz2mol(atomicNumList, charge, xyz_coordinates, charged_fragments, quick)
#
# print(mol)

from torch_geometric.datasets import TUDataset

from tqdm import tqdm

# dataset = TUDataset(root='/tmp/ENZYMES', name='ENZYMES')

# from torch_geometric.datasets import Planetoid
#


# dataset = Planetoid(root='/tmp/Cora', name='Cora')
# print(dataset[0])

# import pandas as pd
# import os
# from torch_geometric.data import Data
# from mol.dataset import Dataset
#
# graphs = './data/mol/structures'
# graphs = [os.path.join(path, graphs) for path in os.listdir(graphs)]
#
# # graphs = {}
# # train = pd.read_csv('./data/mol/train.csv')
# # for _, row in tqdm(train.iterrows()):
# #     if row['molecule_name'] not in graphs:
# #         graphs[row['molecule_name']] = {'x': [], 'edge_index': [], 'edge_attr': [], 'y': []}
# #
# #     graph = graphs[row['molecule_name']]
# #
# #     graph['x'].append()
#
# graphs = [
#     Data(
#         x=graph['x'],
#         edge_index=graph['edge_index'],
#         edge_attr=graph['edge_attr'],
#         y=graph['y']
#     ) for graph in graphs.values()]
#
# dataset = Dataset(graphs)
#
# print(dataset[0])
