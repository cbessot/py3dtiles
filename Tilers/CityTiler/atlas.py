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

        self.height = top - bottom

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

        if (width < self.width and height < self.height) :
            return True
        else : return False
        #comparer les tailles, et ressortir true or not

    def perfect_fits(self,img):

        width, height = img.size

        if (width == self.get_width() and height == self.get_height()):
            return True
        else : return False
        #return si les tailles sont exactements pareils

class Node(object):

    def __init__(self,rect = None):

        self.rect = rect

        self.child = [None,None]

        self.image = None

        self.building_id = None

    def isLeaf(self):

        return (self.child[0] == None and self.child[1] == None)


    def insert(self, img):

        if self.isLeaf() == False :
            newNode = self.child[0].insert(self.image)

            if newNode != None :
                return newNode

            return self.child[1].insert( self.image )

        else :
            if self.image == None:
                return  None

            if self.fits(rect, self.image) == false :
             return None

             if self.perfect_fits(rect, self.image) == True :
                 return self

            self.child[0] = Node()
            self.child[1] = Node()

            width, height = self.image.size

            dw = self.rect.get_width() - width
            dh = self.rect.get_height() - height

            if dw > dh :
                self.child[0].rect = Rectangle(self.rect.get_left(), self.rect.get_top(), self.rect.get_left() + (self.rect.get_width() - 1), self.rect.get_bottom())
                self.child[1].rect = Rectangle(self.rect.get_left() + self.rect.get_width(), self.rect.get_top() , self.rect.get_right(), self.rect.get_bottom())
            else:
                self.child[0].rect = Rectangle(self.rect.get_left(), self.rect.get_top(), self.rect.get_right(), self.rect.get_top() + (self.rect.get_height() - 1))
                self.child[1].rect = Rectangle(self.rect.get_left(), self.rect.get_top() + self.rect.get_height(), self.rect.get_right(), self.rect.get_bottom())

            return self.insert(self.image, self.child[0])
