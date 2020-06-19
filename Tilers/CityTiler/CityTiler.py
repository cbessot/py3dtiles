import argparse
import numpy as np

from py3dtiles import B3dm, BatchTable, BoundingVolumeBox, GlTF
from py3dtiles import Tile, TileSet

from kd_tree import kd_tree
from citym_cityobject import CityMCityObjects
from citym_building import CityMBuildings
from citym_relief import CityMReliefs
from citym_waterbody import CityMWaterBodies
from database_accesses import open_data_base
from database_accesses_batch_table_hierarchy import create_batch_table_hierarchy

import sys
from PIL import Image
from io import BytesIO
from struct import *


def parse_command_line():
    # arg parse
    text = '''A small utility that build a 3DTiles tileset out of the content
               of a 3DCityDB database.'''
    parser = argparse.ArgumentParser(description=text)

    # adding positional arguments
    parser.add_argument('db_config_path',
                        nargs='?',
                        default='CityTilerDBConfig.yml',
                        type=str,  # why precise this if it is the default config ?
                        help='path to the database configuration file')

    parser.add_argument('object_type',
                        nargs='?',
                        default='building',
                        type=str,
                        choices=['building', 'relief', 'water'],
                        help='identify the object type to seek in the database')

    # adding optional arguments
    parser.add_argument('--with_BTH',
                        dest='with_BTH',
                        action='store_true',
                        help='Adds a Batch Table Hierarchy when defined')
    return parser.parse_args()


def create_tile_content(cursor, cityobjects, objects_type):
    """
    :param cursor: a database access cursor.
    :param cityobjects: the cityobjects of the tile.
    :param objects_type: a class name among CityMCityObject derived classes.
                        For example, objects_type can be "CityMBuilding".

    :rtype: a TileContent in the form a B3dm.
    """
    # Get cityobjects ids and the centroid of the tile which is the offset
    cityobject_ids = tuple([cityobject.get_database_id() for cityobject in cityobjects])
    offset = cityobjects.get_centroid()

    arrays = CityMCityObjects.retrieve_geometries(cursor, cityobject_ids, offset, objects_type)
    #arrays = CityMCityObjects.retrieve_texture_coordinates(cursor, cityobject_ids, offset, objects_type)

    # GlTF uses a y-up coordinate system whereas the geographical data (stored
    # in the 3DCityDB database) uses a z-up coordinate system convention. In
    # order to comply with Gltf we thus need to realize a z-up to y-up
    # coordinate transform for the data to respect the glTF convention. This
    # rotation gets "corrected" (taken care of) by the B3dm/gltf parser on the
    # client side when using (displaying) the data.
    # Refer to the note concerning the recommended data workflow
    #    https://github.com/AnalyticalGraphicsInc/3d-tiles/tree/master/specification#gltf-transforms
    # for more details on this matter.
    transform = np.array([1, 0,  0, 0,
                          0, 0, -1, 0,
                          0, 1,  0, 0,
                          0, 0,  0, 1])
    gltf = GlTF.from_binary_arrays(arrays, transform)

    # Create a batch table and add the database ID of each building to it
    bt = BatchTable()

    database_ids = []
    for cityobject in cityobjects:
        database_ids.append(cityobject.get_database_id())

    bt.add_property_from_array("cityobject.database_id", database_ids)

    # When required attach an extension to the batch table
    if objects_type == CityMBuildings and CityMBuildings.is_bth_set():
        bth = create_batch_table_hierarchy(cursor, cityobject_ids)
        bt.add_extension(bth)

    # Eventually wrap the geometries together with the optional
    # BatchTableHierarchy within a B3dm:
    return B3dm.from_glTF(gltf, bt)


def from_3dcitydb(cursor, objects_type):
    """
    :param cursor: a database access cursor.
    :param objects_type: a class name among CityMCityObject derived classes.
                        For example, objects_type can be "CityMBuilding".

    :return: a tileset.
    """

    cityobjects = CityMCityObjects.retrieve_objects(cursor, objects_type)

    if not cityobjects:
        raise ValueError(f'The database does not contain any {objects_type} object')

    # Lump out objects in pre_tiles based on a 2D-Tree technique:
    pre_tiles = kd_tree(cityobjects, 100000)

    tileset = TileSet()
    for tile_cityobjects in pre_tiles:
        tile = Tile()
        tile.set_geometric_error(500)

        # Construct the tile content and attach it to the new Tile:
        tile_content_b3dm = create_tile_content(cursor, tile_cityobjects, objects_type)
        tile.set_content(tile_content_b3dm)

        # The current new tile bounding volume shall be a box enclosing the
        # buildings withheld in the considered tile_cityobjects:
        bounding_box = BoundingVolumeBox()
        for building in tile_cityobjects:
            bounding_box.add(building.get_bounding_volume_box())

        # The Tile Content returned by the above call to create_tile_content()
        # (refer to the usage of the centroid/offset third argument) uses
        # coordinates that are local to the centroid (considered as a
        # referential system within the chosen geographical coordinate system).
        # Yet the above computed bounding_box was set up based on
        # coordinates that are relative to the chosen geographical coordinate
        # system. We thus need to align the Tile Content to the
        # BoundingVolumeBox of the Tile by "adjusting" to this change of
        # referential:
        centroid = tile_cityobjects.get_centroid()
        bounding_box.translate([- centroid[i] for i in range(0,3)])
        tile.set_bounding_volume(bounding_box)

        # The transformation matrix for the tile is limited to a translation
        # to the centroid (refer to the offset realized by the
        # create_tile_content() method).
        # Note: the geographical data (stored in the 3DCityDB) uses a z-up
        #       referential convention. When building the B3dm/gltf, and in
        #       order to comply to the y-up gltf convention) it was necessary
        #       (look for the definition of the `transform` matrix when invoking
        #       `GlTF.from_binary_arrays(arrays, transform)` in the
        #        create_tile_content() method) to realize a z-up to y-up
        #        coordinate transform. The Tile is not aware on this z-to-y
        #        rotation (when writing the data) followed by the invert y-to-z
        #        rotation (when reading the data) that only concerns the gltf
        #        part of the TileContent.
        tile.set_transform([1, 0, 0, 0,
                            0, 1, 0, 0,
                            0, 0, 1, 0,
                           centroid[0], centroid[1], centroid[2], 1])

        # Eventually we can add the newly build tile to the tile set:
        tileset.add_tile(tile)

    # Note: we don't need to explicitly adapt the TileSet's root tile
    # bounding volume, because TileSet::write_to_directory() already
    # takes care of this synchronisation.

    # A shallow attempt at providing some traceability on where the resulting
    # data set comes from:
    cursor.execute('SELECT inet_client_addr()')
    server_ip = cursor.fetchone()[0]
    cursor.execute('SELECT current_database()')
    database_name = cursor.fetchone()[0]
    origin = f'This tileset is the result of Py3DTiles {__file__} script '
    origin += f'run with data extracted from database {database_name} '
    origin += f' obtained from server {server_ip}.'
    tileset.add_asset_extras(origin)

    return tileset

#addition of u and v (simplest solution to find max and min)
def add_uv(uv_tab):
    dataUV_add = uv_tab
    print(" \n ")
    i = 0
    while i < len(dataUV_add):
        y = 0
        while y < len(dataUV_add[i]):
            w = 0
            while w < len(dataUV_add[i][y]):
                dataUV_add[i][y][w] = dataUV_add[i][y][w][0] + dataUV_add[i][y][w][1]
                w+=1
            y+=1
        i+=1
    return(dataUV_add)
#returning the index of uv max
def searchMin(uv_tab_add):
    if len(uv_tab_add) != 0:
        i = 0
        minimum = uv_tab_add[0]
        while i < len(uv_tab_add):
            if uv_tab_add[i] < minimum :
                minimum = uv_tab_add[i]
            i+=1
        return uv_tab_add.index(minimum)
    else :
        return ('Error : parameter \'tab\' is empty')
#returning the index of uv max
def searchMax(uv_tab_add):

    if len(uv_tab_add) != 0:
        i = 0
        maximum = uv_tab_add[0]
        while i < len(uv_tab_add):
            if uv_tab_add[i] > maximum :
                maximum = uv_tab_add[i]
            i+=1
        return uv_tab_add.index(maximum)
    else :
        return ('Error : parameter \'tab\' is empty')
#find min value uv about his index
def findMin(data):
    tab_of_index_min = []
    y = 0
    while y < len(data):
        indexOfOneBuildings = []
        dataAdd = add_uv(data[0][y])
        i = 0
        while i < len(dataAdd):
                indexOfOneBuildings.append(searchMin(dataAdd[i][0]))
                i+=1
        tab_of_index_min.append(indexOfOneBuildings)
        y+=1
    return tab_of_index_min
#find max value uv about his index
def findMax(data):
    tab_of_index_max = []
    y = 0
    while y < len(data):
        indexOfOneBuildings = []
        dataAdd = add_uv(data[0][y])
        i = 0
        while i < len(dataAdd):
                indexOfOneBuildings.append(searchMax(dataAdd[i][0]))
                i+=1
        tab_of_index_max.append(indexOfOneBuildings)
        y+=1

    return tab_of_index_max
    """i = 0
    while i < len(data[0]):
        y = 0
        while y < len(data[0][i]):
            print(data[0][i][y][0][maximums[i][y]])
            y+=1
        print('\n \n')
        i+=1"""

def tab_u(data):
    tab_u = []
    i = 0
    while i < len(data[0]):
        y = 0
        tab_u_geom = []
        while y < len(data[0][i]):
            z = 0
            tab_u_list = []
            while z < len(data[0][i][y][0]):
                tab_u_list.append(data[0][i][y][0][z][0])
                z+=1
            tab_u_geom.append(tab_u_list)
            y+=1
        tab_u.append(tab_u_geom)
        i+=1
    return tab_u

def tab_v(data):
    tab_v = []
    i = 0
    while i < len(data[0]):
        y = 0
        tab_v_geom = []
        while y < len(data[0][i]):
            z = 0
            tab_v_list = []
            while z < len(data[0][i][y][0]):
                tab_v_list.append(data[0][i][y][0][z][1])
                z+=1
            tab_v_geom.append(tab_v_list)
            y+=1
        tab_v.append(tab_v_geom)
        i+=1
    return tab_v

def min_tab(tab):
    tab_min = []
    tab_min_builings = []
    i = 0
    while i < len(tab):
        y = 0
        tab_min_geom = []
        while y < len(tab[i]):
            minimum = np.min(tab[i][y])
            tab_min_geom.append(minimum)
            y+=1
        tab_min.append(tab_min_geom)
        i+=1
    return tab_min

def max_tab(tab):
    tab_max = []
    tab_max_builings = []
    i = 0
    while i < len(tab):
        y = 0
        tab_max_geom = []
        while y < len(tab[i]):
            maximum = np.max(tab[i][y])
            tab_max_geom.append(maximum)
            y+=1
        tab_max.append(tab_max_geom)
        i+=1
    return tab_max


def main():
    """
    :return: no return value

    this function creates a repository name "junk_object_type" where the
    tileset is stored.
    """
    args = parse_command_line()
    cursor = open_data_base(args.db_config_path)

    if args.object_type == "building":
        objects_type = CityMBuildings
        if args.with_BTH:
            CityMBuildings.set_bth()
    elif args.object_type == "relief":
        objects_type = CityMReliefs
    elif args.object_type == "water":
        objects_type = CityMWaterBodies

    tileset = from_3dcitydb(cursor, objects_type)

    #list of buildings
    """buildings = (3, 4)

    #récupération des URI des textures
    data = CityMCityObjects.retrieve_texture_coordinates(cursor,buildings,CityMBuildings)
    tab_u_var = tab_u(data)
    tab_min_u = min_tab(tab_u_var)
    tab_max_u = max_tab(tab_u_var)

    tab_v_var = tab_v(data)
    tab_min_v = min_tab(tab_v_var)
    tab_max_v = max_tab(tab_v_var)

    #Récupération des données binaires des textures associées à la liste de buildings grâce aux URI des images | retour -> liste contenant les bonnes textures sous forme de données bninaires
    image_uri_list = data[1]
    imageDataList = []
    i = 0
    while i < len(image_uri_list):
        imageDataList.append(CityMCityObjects.retrieve_textures(cursor,image_uri_list[i],CityMBuildings))
        i+=1

    #Determiner les UVs maximums et minimums pour chaques géométries de chaques bâtiments
    maximums = findMax(data)
    data = CityMCityObjects.retrieve_texture_coordinates(cursor,buildings,CityMBuildings)
    minimums = findMin(data)
    data = CityMCityObjects.retrieve_texture_coordinates(cursor,buildings,CityMBuildings)
    i = 0
    z = 0
    while i < len(tab_u_var):
        y = 0
        LEFT_THUMB = imageDataList[i][0][0]
        stream = BytesIO(LEFT_THUMB)
        image = Image.open(stream).convert("RGBA")
        while y < len(tab_u_var[i]):
            width , height = image.size
            max_width = tab_max_u[i][y] * width
            max_height = tab_max_v[i][y] * height
            min_width = tab_min_u[i][y] * width
            min_height = tab_min_v[i][y] * height
            print(width , height)
            print(min_width , min_height , max_width , max_height)
            y+=1
            if min_height >= max_height  or min_width >= max_width : continue
            forkImage = image.crop((min_width , min_height , max_width , max_height))
            #tream.close()
            forkImage.save('textures_extract/texture_' + str(i) + '_' + str(y) + '.png' )
            print(y)
            z+=1
        i+=1"""

    #CityMCityObjects.retrieve_geometries(cursor,builings,CityMBuildings)
    cursor.close()
    tileset.get_root_tile().set_bounding_volume(BoundingVolumeBox())
    if args.object_type == "building":
        tileset.write_to_directory('junk_buildings')
    elif args.object_type == "relief":
        tileset.write_to_directory('junk_reliefs')
    elif args.object_type == "water":
        tileset.write_to_directory('junk_water_bodies')






if __name__ == '__main__':
    main()
