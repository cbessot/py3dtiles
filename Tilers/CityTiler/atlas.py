import sys
import numpy as np
from PIL import Image, ImageDraw
from py3dtiles import BoundingVolumeBox, TriangleSoup

class Rectangle(object):

    def __init__(self, left, top, right, bottom):

        self.left = left

        self.right = right

        self.top = top

        self.bottom = bottom

        self.width = right - left

        self.height = bottom - top

    def get_top(self):

        return self.top

    def get_bottom(self):

        return self.bottom

    def get_right(self):

        return self.right

    def get_left(self):
        return self.left

    def get_width(self):

        return self.width

    def get_height(self):

        return self.height

    def fits(self,img):

        width, height = img.size
        if (width <= (self.width) and (height <= self.height)) :
            return True
        else :
            return False

    def perfect_fits(self,img):

        width, height = img.size
        if (width == self.get_width() and height == self.get_height()):
            return True
        else : return False

class Node(object):

    def __init__(self,rect = None):

        self.rect = rect

        self.child = [None,None]

        self.image = None

        self.building_id = None

    def isLeaf(self):

        return (self.child[0] == None and self.child[1] == None)

    def insertImages(self, atlas , geom):

        if self.isLeaf():

            if self.image != None :
                print(self.image.size)
                atlas.paste(self.image, (self.rect.get_left(), self.rect.get_top()))
                self.updateUv(geom[self.building_id].triangles[1], self.image, atlas)
        else :
            self.child[0].insertImages(atlas, geom)
            self.child[1].insertImages(atlas, geom)


    def updateUv(self, uvs, oldTexture, newTexture):

        oldWidth, oldHeight= (oldTexture.size)
        newWidth, newHeight= (newTexture.size)

        ratioWidth = oldWidth/newWidth
        ratioHeight = oldHeight/newHeight

        offsetWidth = (self.rect.get_left()/newWidth)
        offsetHeight = (self.rect.get_top()/newHeight)

        for i in range(0,len(uvs)):

            for y in range(0,3):
                new_u = ((uvs[i][y][0] * oldWidth) / newWidth) + offsetWidth
                new_v = ((uvs[i][y][1] * oldHeight) / newHeight) + offsetHeight
                uvs[i][y] = np.array([new_u, new_v], dtype=np.float32)


    def insert(self, img, building_id):

        if self.isLeaf()==False:
            newNode = self.child[0].insert(img, building_id)

            if newNode != None :
                self.child[0] = newNode
                return self
            else:
                newNode = self.child[1].insert(img, building_id)
                if newNode != None:
                    self.child[1] = newNode
                    return self
                else:
                    return None

        else :
            if self.image != None:
                return  None

            if self.rect.perfect_fits(img) == True :
                self.building_id = building_id
                self.image = img
                return self

            if self.rect.fits(img) == False :
                return None

            self.child[0] = Node()
            self.child[1] = Node()

            width, height = img.size

            dw = self.rect.get_width() - width
            dh = self.rect.get_height() - height

            if dw > dh :
                self.child[0].rect = Rectangle(self.rect.get_left(),self.rect.get_top(),self.rect.get_left() + width, self.rect.get_bottom())
                self.child[1].rect = Rectangle(self.rect.get_left() + width + 1, self.rect.get_top() ,self.rect.get_right(), self.rect.get_bottom())
            if dw < dh:
                self.child[0].rect = Rectangle(self.rect.get_left(), self.rect.get_top(), self.rect.get_right(), self.rect.get_top() + height)
                self.child[1].rect = Rectangle(self.rect.get_left(), self.rect.get_top() + height + 1, self.rect.get_right(), self.rect.get_bottom())

            self.child[0].insert(img, building_id)
            return self
