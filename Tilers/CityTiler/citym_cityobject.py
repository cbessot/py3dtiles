# -*- coding: utf-8 -*-
import sys
import numpy as np
import struct
from PIL import Image, ImageDraw
from io import BytesIO
from py3dtiles import BoundingVolumeBox, TriangleSoup
from atlas import Rectangle, Node
import math
from math import *


class CityMCityObject(object):
    """
    The base class of all thematic classes within CityGML’s data model is the abstract class
    _CityObject. (cf 3DCityDB Version 3.3.0 Documentation).
    """
    def __init__(self, database_id=None, box_in=None):
        """
        :param id: given identifier
        :param box_2D: the maximum extents of the geometry a returned by a
                       PostGis::Box3D(geometry geomA) call (refer to
                       https://postgis.net/docs/Box3D.html) that is a string
                       of the form 'BOX3D(1 2 3, 4 5 6)' where:
                        * 1, 2 and 3 are the respective minimum of X, Y and Z
                        * 4, 5 and 6 are the respective maximum of X, Y and Z
        """

        # The identifier of the database
        self.database_id = None

        # The City GML identifier (out of the "original" CityGML data file)
        self.gml_id = None

        # A Bounding Volume Box object
        self.box = None

        # The centroid of the box
        self.centroid = None

        if database_id:
            self.set_database_id(database_id)
        if box_in:
            self.set_box(box_in)

    def set_box(self, box_in):
        # Realize the following convertion:
        # 'BOX3D(1 2 3, 4 5 6)' -> [[1, 2, 3], [4, 5, 6]]
        box_parsed = [[float(coord) for coord in point.split(' ')]
                                    for point in box_in[6:-1].split(',')]
        x_min = box_parsed[0][0]
        x_max = box_parsed[1][0]
        y_min = box_parsed[0][1]
        y_max = box_parsed[1][1]
        z_min = box_parsed[0][2]
        z_max = box_parsed[1][2]

        self.box = BoundingVolumeBox()
        self.box.set_from_mins_maxs([x_min, y_min, z_min, x_max, y_max, z_max])
        # Centroid of the box
        self.centroid = [(x_min + x_max) / 2.0,
                         (y_min + y_max) / 2.0,
                         (z_min + z_max) / 2.0]

    def set_database_id(self, id):
        self.database_id = id

    def get_database_id(self):
        return self.database_id

    def set_gml_id(self, gml_id):
        self.gml_id = gml_id

    def get_gml_id(self):
        """
        :return: the (city)gml identifier of an object that should be encountered
                in the database.
        """
        return self.gml_id

    def get_centroid(self):
        return self.centroid

    def get_bounding_volume_box(self):
        return self.box


class CityMCityObjects:
    """
    A decorated list of CityMCityObject type objects.
    """
    def __init__(self, cityobjects=None):
        self.cityobjects = list()
        if cityobjects:
            self.cityobjects.extend(cityobjects)

    def __iter__(self):
        return iter(self.cityobjects)

    def __getitem__(self, item):
        if isinstance(item, slice):
            return CityMCityObjects(self.cityobjects.__getitem__(item))
        # item is then an int type:
        return self.cityobjects.__getitem__(item)

    def __add__(self, other):
        new_cityobject = CityMCityObjects(self.cityobjects)
        new_cityobject.cityobjects.extend(other.cityobjects)
        return new_cityobject

    def append(self, cityobject):
        self.cityobjects.append(cityobject)

    def extend(self, others):
        self.cityobjects.extend(others)

    def get_city_objects(self):
        return self.cityobjects

    def __len__(self):
        return len(self.cityobjects)

    def get_centroid(self):
        centroid = [0., 0., 0.]
        for cityobject in self:
            centroid[0] += cityobject.get_centroid()[0]
            centroid[1] += cityobject.get_centroid()[1]
            centroid[2] += cityobject.get_centroid()[2]
        return [centroid[0] / len(self),
                centroid[1] / len(self),
                centroid[2] / len(self)]

    @staticmethod
    def sql_query_objects():
        """
        Virtual method: all CityMCityObjects and childs classes instances should
        implement this method.

        :return: no return value.
        """
        pass

    @staticmethod
    def retrieve_objects(cursor, objects_type, cityobjects=list()):
        """
        :param cursor: a database access cursor.
        :param objects_type: a class name among CityMCityObject derived classes.
                        For example, objects_type can be "CityMBuilding".

        :param cityobjects: a list of objects_type type object that should be
                        sought in the database. When this list is empty all
                        the objects encountered in the database are returned.

        :return: an objects_type type object containing the objects that were retrieved
                in the 3DCityDB database, each object being decorated with its database
                identifier as well as its 3D bounding box (as retrieved in the database).
        """
        if not cityobjects:
            no_input = True
        else:
            no_input = False

        cursor.execute(objects_type.sql_query_objects(cityobjects))

        if no_input:
            result_objects = objects_type()
        else:
            # We need to deal with the fact that the answer will (generically)
            # not preserve the order of the objects that was given to the query
            objects_with_gmlid_key = dict()
            for cityobject in cityobjects:
                objects_with_gmlid_key[cityobject.gml_id] = cityobject

        for t in cursor.fetchall():
            object_id = t[0]
            if not t[1]:
                print("Warning: object with id ", object_id)
                print("         has no 'cityobject.envelope'.")
                if no_input:
                    print("     Dropping this object (downstream trouble ?)")
                    continue
                print("     Exiting (is the database corrupted ?)")
                sys.exit(1)
            box = t[1]
            if no_input:
                new_object = CityMCityObject(object_id, box)
                result_objects.append(new_object)
            else:
                gml_id = t[2]
                cityobject = objects_with_gmlid_key[gml_id]
                cityobject.set_database_id(object_id)
                cityobject.set_box(box)
        if no_input:
            return result_objects
        else:
            return cityobjects

    @staticmethod
    def sql_query_geometries():
        """
        Virtual method: all CityMCityObjects and childs classes instances should
        implement this method.

        :return: no return value.
        """
        pass

    @staticmethod
    def retrieve_textures(cursor, image_uri, objects_type):
        """
        :param cursor: a database access cursor
        :param city_object_ids: a list of (city)gml identifier corresponding to
                       objects_type type objects whose geometries are sought.
        :param objects_type: a class name among CityMCityObject derived classes.
                        For example, objects_type can be "CityMBuilding".
        :rtype List[Dict]: a TileContent in the form a B3dm.
        """
        res = []
        city_object_ids_arg = str(image_uri).replace(',)', ')')
        cursor.execute(objects_type.sql_query_textures(image_uri))
        for t in cursor.fetchall():
            res.append(t)
        return(res)


    @staticmethod
    def retrieve_geometries(cursor, city_object_ids, offset, objects_type):
        """
        :param cursor: a database access cursor
        :param city_object_ids: a list of (city)gml identifier corresponding to
                       objects_type type objects whose geometries are sought.
        :param offset: the offset (a 3D "vector" of floats) by which the
                       geographical coordinates should be translated (the
                       computation is done at the GIS level).
        :param objects_type: a class name among CityMCityObject derived classes.
                        For example, objects_type can be "CityMBuilding".
        :rtype List[Dict]: a TileContent in the form a B3dm.
        """
        city_object_ids_arg = str(city_object_ids).replace(',)', ')')

        cursor.execute(objects_type.sql_query_geometries(offset,
                                                         city_object_ids_arg))


        # Deal with the reordering of the retrieved geometries
        city_objects_with_gmlid_key = dict()
        textures_with_building_id_key = dict()
        surfaceTotal = 0
        for t in cursor.fetchall():
            tab_textures_id_batiments = []
            city_object_root_id = t[0]
            geom_as_string = t[1]
            uv_as_string = t[2]
            array = []
            texture_uri = t[3]
            array.append(uv_as_string)
            if geom_as_string is None:
                # Some thematic surface mimageConvertay have no geometry (due to a cityGML
                # exporter bug?): simply ignore them.
                print("Warning: no valid geometry in database.")
                sys.exit(1)
            geom = TriangleSoup.from_wkb_multipolygon(geom_as_string, array)
            geom.triangles.append(texture_uri)
            uvs = geom.triangles[1]

            #dataBinary to png
            forkImage = imageConvertToPng(geom.triangles[2], objects_type, cursor)

            #Size of texture and surface
            (width , height) = forkImage.size
            surface = width * height
            surfaceTotal+=surface

            #dict with building_id and texture associated
            textures_with_building_id_key[city_object_root_id] = forkImage

            if len(geom.triangles[0]) == 0:
                print("Warning: empty (no) geometry from the database.")
                sys.exit(1)
            city_objects_with_gmlid_key[city_object_root_id] = geom

        sorted_atlas = sorted(textures_with_building_id_key.items(), key=lambda t: calculationSurface(t[1].size), reverse=True)
        sizeOfAtlas = multipleOf2(np.sqrt(surfaceTotal))
        rect = Rectangle(0,0,sizeOfAtlas,sizeOfAtlas)
        node_root = None
        while node_root == None :
            node_root = Node(rect)
            for key, image in sorted_atlas:
                node_root = node_root.insert(image,key)
                if node_root == None:
                    rect = Rectangle(0,0,rect.get_width() * 2, rect.get_height() * 2)
                    break

        #empty image which will be the basis of the atlas | it is the size of the rectangle
        atlas = Image.new('RGB', (node_root.rect.get_width(), node_root.rect.get_width()), color = 'black')

        node_root.insertImages(atlas, city_objects_with_gmlid_key)
        atlas.save('junk_buildings/tiles/ATLAS_' + str(key) + '.png')

        # Package the geometries within a data structure that the
        # GlTF.from_binary_arrays() function (see below) expects to consume:
        arrays = []
        for incoming_id in city_object_ids:
            geom = city_objects_with_gmlid_key[incoming_id]
            arrays.append({
                'position': geom.getPositionArray(),
                'normal': geom.getNormalArray(),
                'bbox': [[float(i) for i in j] for j in geom.getBbox()],
                'uv': geom.getDataArray(0)
            })
        return arrays, key

def calculationSurface(size):
    width, height = size
    return width * height

def imageConvertToPng(textureUri, objects_type, cursor):
    imageBinaryData = objects_type.retrieve_textures(cursor, textureUri, objects_type)
    LEFT_THUMB = imageBinaryData[0][0]
    stream = BytesIO(LEFT_THUMB)
    image = Image.open(stream).convert("RGBA")
    return image

def multipleOf2(nb):
    i = 1
    while i < nb :
        i*=2
    return i
