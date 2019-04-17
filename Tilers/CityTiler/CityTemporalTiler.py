import argparse
import numpy as np
import sys
from pprint import pprint

from py3dtiles import B3dm, BatchTable
from py3dtiles import BoundingVolumeBox, TemporalBoundingVolume
from py3dtiles import GlTF
from py3dtiles import Tile, TileSet, TemporalTileSet
from py3dtiles import TemporalTransaction

from temporal_utils import debug_msg, debug_msg_ne
from temporal_graph import TemporalGraph

from temporal_building import TemporalBuilding
from building import Buildings
from kd_tree import kd_tree
from database_accesses import open_data_bases, retrieve_geometries, \
                              get_buildings_from_3dcitydb


def ParseCommandLine():
    # arg parse
    descr = '''A small utility that build a 3DTiles temporal tileset out of 
               - temporal data of the buildings
               - the content of a 3DCityDB databases.'''
    parser = argparse.ArgumentParser(description=descr)
    parser.add_argument('--db_config_path',
                        nargs='+',
                        default='CityTilerDBConfig.yml',
                        type=str,
                        help='Path(es) to the database configuration file(s)')
    parser.add_argument('--time_stamps',
                        nargs='+',
                        type=str,
                        help='Time stamps (corresponding to each database)')
    parser.add_argument('--temporal_graph',
                        nargs='+',
                        type=str,
                        help='GraphML-Json temporal data filename(s)')
    parser.add_argument('--with_BTH',
                        dest='with_BTH',
                        action='store_true',
                        help='Adds a Batch Table Hierachy when defined')
    result = parser.parse_args()

    if len(result.db_config_path) <= 1:
        print("Only a single database configuration file was provided.")
        print("This is highly suspect since temporal comparisons require at")
        print("lest to time-stamps and thus two databases (one for each).")
        print("Exiting.")
        sys.exit(1)
    else:
        # When there is more than one database there should be as
        # as many time stamps as databases (because each time stamp
        # corresponds to a database:
        if not result.time_stamps:
            # How come the nargs+ doesn't deal with this case ?
            print("There must be as many time-stamps as databases.")
            print("Provide time-stamps with the --time_stamps option.")
            sys.exit(1)
        if len(result.db_config_path) != len(result.time_stamps):
            print("Mismatching number of databases vs time-stamps:")
            print(" - databases (configurations): ", result.db_config_path)
            print(" - timestamps: ", result.time_stamps)
            print("Exiting.")
            sys.exit(1)
    return result


def create_tile_content(cursors, buildings, offset):
    """
    :param cursors: a dictionary with a timestamp as key and database cursors
                    as values
    :param buildings: a Buildings object
    :param offset: the offset (a a 3D "vector" of floats) by which the
                   geographical coordinates should be translated (the
                   computation is done at the GIS level)
    :rtype: a TileContent in the form a B3dm.
    """
    # We have to fan out the retrieval of the geometries (because buildings
    # belong to different databases)

    time_stamped_buildings = dict()
    for time_stamp in cursors.keys():
        time_stamped_buildings[time_stamp] = list()
    for building in buildings:
        time_stamped_buildings[building.get_time_stamp()].append(building)

    arrays = []
    for time_stamp, buildings in time_stamped_buildings.items():
        if not buildings:
            continue
        building_database_ids = tuple(
            [building.get_database_id() for building in buildings])
        arrays.extend(retrieve_geometries(cursors[time_stamp],
                                          building_database_ids,
                                          offset))

    # GlTF uses a y-up coordinate system whereas the geographical data (stored
    # in the 3DCityDB database) uses a z-up coordinate system convention. In
    # order to comply with Gltf we thus need to realize a z-up to y-up
    # coordinate transform for the data to respect the glTF convention. This
    # rotation gets "corrected" (taken care of) by the B3dm/gltf parser on the
    # client side when using (displaying) the data.
    # Refer to the note concerning the recommended data workflow
    #    https://github.com/AnalyticalGraphicsInc/3d-tiles/tree/master/specification#gltf-transforms
    # for more details on this matter.
    transform = np.array([1, 0, 0, 0,
                          0, 0, -1, 0,
                          0, 1, 0, 0,
                          0, 0, 0, 1])
    gltf = GlTF.from_binary_arrays(arrays, transform)

    # Eventually wrap the geometries together within a B3dm:
    return B3dm.from_glTF(gltf)


def extract_bounding_dates(buildings):
    """
    :param buildings: a set (Buildings object) of buildings
    :return: the earliest creation date and the latest deletion dates found
             among the given buildings
    """
    # Initialize with whatever value
    creation_date = buildings[0].get_creation_date()
    deletion_date = buildings[0].get_deletion_date()
    for building in buildings:
        if building.get_creation_date() < creation_date:
            creation_date = building.get_creation_date()
        if building.get_deletion_date() > deletion_date:
            deletion_date = building.get_deletion_date()
    return {'creation_date': creation_date, 'deletion_date': deletion_date}


def from_3dcitydb(cursors, buildings):
    """
    :param cursors: a dictionary with a timestamp as key and database cursors
                    as values
    :param buildings: a Buildings object
    """
    # Lump out buildings in pre_tiles based on a 2D-Tree technique:
    debug_msg_ne('2D-Tree sorting: launching')
    pre_tiles = kd_tree(buildings, 20)
    debug_msg('2D-Tree sorting: done.    ')

    tileset = TileSet()
    debug_msg('TileSet creation:')
    for debug_index, tile_buildings in enumerate(pre_tiles):
        debug_msg_ne(f'  Creating tile {debug_index+1} / {len(pre_tiles)}')
        tile = Tile()
        tile.set_geometric_error(500)

        # Construct the tile content and attach it to the new Tile:
        centroid = tile_buildings.getCentroid()
        tile_content_b3dm = create_tile_content(cursors,
                                                tile_buildings,
                                                centroid)
        tile.set_content(tile_content_b3dm)

        # The current new tile bounding volume shall be a box enclosing the
        # buildings withheld in the considered tile_buildings:
        bounding_box = BoundingVolumeBox()
        for building in tile_buildings:
            bounding_box.add(building.getBoundingVolumeBox())

        # The Tile Content returned by the above call to create_tile_content()
        # (refer to the usage of the centroid/offset third argument) uses
        # coordinates that are local to the centroid (considered as a
        # referential system within the chosen geographical coordinate system).
        # Yet the above computed bounding_box was set up based on
        # coordinates that are relative to the chosen geographical coordinate
        # system. We thus need to align the Tile Content to the
        # BoundingVolumeBox of the Tile by "adjusting" to this change of
        # referential:
        bounding_box.translate([- centroid[i] for i in range(0, 3)])
        temporal_bv = TemporalBoundingVolume()
        bounding_dates = extract_bounding_dates(tile_buildings)
        temporal_bv.set_start_date(bounding_dates['creation_date'])
        temporal_bv.set_end_date(bounding_dates['deletion_date'])
        bounding_box.add_extension(temporal_bv)
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

    debug_msg(f'  Creating tile {debug_index+1} / {len(pre_tiles)}: done.')

    # Note: we don't need to explicitly adapt the TileSet's root tile
    # bounding volume, because TileSet::write_to_directory() already
    # takes care of this synchronisation.

    # A shallow attempt at providing some traceability on where the resulting
    # data set comes from:
    origin = f'This tileset is the result of Py3DTiles {__file__} script '
    origin += 'ran with data extracted from the following databases:'
    for cursor in cursors.values():
        cursor.execute('SELECT inet_client_addr()')
        server_ip = cursor.fetchone()[0]
        cursor.execute('SELECT current_database()')
        database_name = cursor.fetchone()[0]
        origin += '   - ' + server_ip + ': ' + database_name + '\n'
    tileset.add_asset_extras(origin)

    return tileset


def combine_nodes_with_buildings_from_3dcitydb(graph, cursors):
    # ######## Convert the nodes to buildings (optimization purpose)
    # Constructing the pre-tiling stage (i.e. sorting out the cityGML objects
    # in a 2D-Tree used as input to the TileSet construction per-se, refer to
    # to the from_3dcitydb() method) requires the objects bounding boxes. Once
    # retrieved we would have to match the retrieved building with the upcoming
    # nodes in order to transfer the temporal information (creation/deletion
    # dates). In order to avoid this possibly expensive matching, we create
    # temporal buildings and let from_3dcitydb() decorate those objects with
    # the information it extracts from the database:
    resulting_buildings = Buildings()
    for index, time_stamp in enumerate(cli_args.time_stamps):
        cursor = cursors[index]
        nodes = graph.get_nodes_with_time_stamp(time_stamp)
        buildings = Buildings()
        for node in nodes:
            new_building = TemporalBuilding()
            new_building.set_creation_date(node.get_creation_date())
            new_building.set_deletion_date(node.get_deletion_date())
            new_building.set_temporal_id(node.get_global_id())
            new_building.set_gml_id(node.get_local_id())
            buildings.append(new_building)
        extracted_buildings = get_buildings_from_3dcitydb(cursor, buildings)
        resulting_buildings.extend(extracted_buildings.buildings)
    return resulting_buildings

def build_temporal_tile_set(graph):
    # ####### We are left with transposing the information carried by the
    # graph edges to transactions
    debug_msg(f'  Creating transactions')
    temporal_tile_set = TemporalTileSet()
    for edge in graph.edges:
        if not edge.is_modified():
            continue
        transaction = TemporalTransaction()
        ancestor = edge.get_ancestor()
        descendant = edge.get_descendant()
        transaction.set_id('dummy_id_for_modified_edge_case')
        transaction.set_start_date(ancestor.get_deletion_date())
        transaction.set_end_date(descendant.get_creation_date())
        transaction.set_type('replace')
        transaction.append_tag('modified')
        transaction.append_old_feature(ancestor.get_global_id())
        transaction.append_new_feature(descendant.get_global_id())
        temporal_tile_set.append_transaction(transaction)

    # ####### The fusion case
    time_stamps = graph.extract_time_stamps()
    for time_stamp in time_stamps:
        current_nodes = graph.get_nodes_with_time_stamp(time_stamp)
        for node in current_nodes:
            if not node.are_all_ancestor_edges_fusion_typed():
                continue

            transaction = TemporalTransaction()
            transaction.set_id('dummy_id_for_fusion_node_case')
            transaction.set_type('replace')
            transaction.append_new_feature(node.get_global_id())

            if not node.do_all_ancestor_nodes_share_same_date():
                debug_msg("Warning: fusion transaction surely erroneous...")
                transaction.set_id('dummy_id_ERRONEOUS_fusion_node_case')
            # We here make the assumption that all ancestor nodes all share
            # the same deletion date for the following code to make sense:
            some_ancestor = node.get_ancestors()[0]
            transaction.set_start_date(some_ancestor.get_deletion_date())
            transaction.set_end_date(node.get_creation_date())

            for ancestor in node.get_ancestors():
                transaction.append_tag('fusion')
                transaction.append_old_feature(ancestor.get_global_id())

            temporal_tile_set.append_transaction(transaction)

    # ####### The subdivision case
    for time_stamp in time_stamps:
        current_nodes = graph.get_nodes_with_time_stamp(time_stamp)
        for node in current_nodes:
            if not node.are_all_descendant_edges_subdivision_typed():
                continue

            transaction = TemporalTransaction()
            transaction.set_id('dummy_id_for_fusion_node_case')
            transaction.set_type('replace')
            transaction.append_old_feature(node.get_global_id())

            if not node.do_all_descendant_nodes_share_same_date():
                debug_msg("Warning: erroneous subdivision transaction ?")
                transaction.set_id('dummy_id_ERRONEOUS_subdivision_node_case')
            # We here make the assumption that all descendant nodes all share
            # the same deletion date for the following code to make sense:
            some_descendant = node.get_descendants()[0]
            transaction.set_end_date(some_descendant.get_creation_date())
            transaction.set_start_date(node.get_deletion_date())

            for descendant in node.get_descendants():
                transaction.append_tag('subdivision')
                transaction.append_new_feature(descendant.get_global_id())

            temporal_tile_set.append_transaction(transaction)

    return temporal_tile_set


if __name__ == '__main__':

    cli_args = ParseCommandLine()

    # #### Reconstruct the graph
    graph = TemporalGraph(cli_args)
    graph.reconstruct_connectivity()
    debug_msg("Reconstructed graph characteristics:")
    graph.display_characteristics('   ')
    graph.simplify(display_characteristics=True)
    debug_msg("")
    # graph.print_nodes_and_edges()

    # #### Extract the information form the databases
    # TODO: make a test asserting the coherence between
    # graph.extract_time_stamps() and cli_args.time_stamps
    cursors = open_data_bases(cli_args.db_config_path)
    time_stamped_cursors = dict()
    for index in range(len(cursors)):
        time_stamped_cursors[cli_args.time_stamps[index]] = cursors[index]

    all_buildings = combine_nodes_with_buildings_from_3dcitydb(graph, cursors)

    # #### Construct the temporal tile set:
    tile_set = from_3dcitydb(time_stamped_cursors, all_buildings)

    [cursor.close() for cursor in cursors] # We are done with the databases

    temporal_tile_set = build_temporal_tile_set(graph)
    tile_set.add_extension(temporal_tile_set)

    tile_set.write_to_directory('junk')

